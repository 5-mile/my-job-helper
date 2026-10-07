// 텔레그램 버튼 처리 봇 (Supabase Edge Function).
//
// 텔레그램이 버튼 누름·메시지를 이 함수로 보내면(webhook), 앱과 같은 DB에서 바로 처리한다.
// 응답 본문에 {"method": ...} 를 실어 보내면 텔레그램이 그걸 실행하므로, 봇 토큰을 여기 둘 필요가 없다.
//
// 보안: setWebhook 때 정한 비밀값(X-Telegram-Bot-Api-Secret-Token)이 맞고,
// 등록된 내 채팅에서 온 것만 처리한다. 둘 다 DB의 bot_config 에 있다 (telegram_bot.py 가 넣는다).
//
// 배포·설정: python telegram_bot.py setup

import postgres from "npm:postgres@3.4.5";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { prepare: false, max: 1 });

const MENU = {
  keyboard: [[{ text: "📋 공고 더 보기" }, { text: "⏰ 마감 임박" }, { text: "📁 보관함" }]],
  resize_keyboard: true,
  is_persistent: true,
};
const BATCH = 5;
const SKIP_STATUSES = ["최종 합격", "불합격"];

type Job = Record<string, string>;

// 한국 시간 기준 날짜·시각 (앱이 쓰는 now_iso 와 같은 모양)
const kst = () => new Date(Date.now() + 9 * 3600_000).toISOString();
const today = () => kst().slice(0, 10);
const plusDays = (d: string, n: number) =>
  new Date(Date.parse(d) + n * 86400_000).toISOString().slice(0, 10);

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });

async function config(key: string): Promise<string | undefined> {
  const rows = await sql`select value from bot_config where key = ${key}`;
  return rows[0]?.value;
}

// digest.py 의 build_message 와 같은 모양
function formatJobs(jobs: Job[], title: string): string {
  const lines = [`📋 ${title} ${jobs.length}건`, ""];
  jobs.forEach((j, i) => {
    const cond = [j.career, j.education, j.employment].filter(Boolean).join(" · ");
    lines.push(
      `${i + 1}. ${j.company} · ${(j.location || "").split(",")[0]}`,
      `   ${j.position}`,
      `   ${cond} · 마감 ${j.deadline || "상시/상세 확인"}`,
      `   ${j.link}`,
      "",
    );
  });
  lines.push("⭐ 번호를 누르면 보관함에 저장됩니다.");
  return lines.join("\n");
}

function jobButtons(keys: string[]) {
  return {
    inline_keyboard: [
      keys.map((k, i) => ({ text: `⭐${i + 1}`, callback_data: `s:${k}` })),
      [{ text: "다음 5건 ▶", callback_data: "m" }],
    ],
  };
}

// 📋 공고 더 보기 — 매일 모아 둔 후보 중 아직 안 보낸 것
async function more(chatId: number) {
  const rows = await sql`
    select job_key, data from digest_pool
    where sent_at is null and (deadline = '' or deadline >= ${today()})
    order by score desc, collected_at desc limit ${BATCH}`;
  if (rows.length === 0) {
    return {
      method: "sendMessage", chat_id: chatId, reply_markup: MENU,
      text: "새로 보여드릴 공고가 없습니다. 매일 아침 9시에 새로 모읍니다.",
    };
  }
  const keys = rows.map((r) => r.job_key as string);
  await sql`update digest_pool set sent_at = ${kst().slice(0, 19)} where job_key in ${sql(keys)}`;
  return {
    method: "sendMessage", chat_id: chatId, disable_web_page_preview: true,
    text: formatJobs(rows.map((r) => JSON.parse(r.data)), "공고"),
    reply_markup: jobButtons(keys),
  };
}

// ⭐ — 그 공고를 앱 보관함(scrapped_jobs)에 넣는다. db.save_job 과 같은 컬럼.
async function save(callbackId: string, key: string) {
  const rows = await sql`select data from digest_pool where job_key = ${key}`;
  if (rows.length === 0) {
    return { method: "answerCallbackQuery", callback_query_id: callbackId, text: "공고 정보를 찾지 못했습니다." };
  }
  const j: Job = JSON.parse(rows[0].data);
  const added = await sql`
    insert into scrapped_jobs
      (source, company, position, date, link, location, category, rating, welfares,
       deadline, status, memo, applied_at, created_at)
    values
      ('텔레그램', ${j.company}, ${j.position}, ${j.date || ""}, ${j.link}, ${j.location || ""},
       ${j.category || ""}, 3.0, '', ${j.deadline || ""}, '관심', '', '', ${kst().slice(0, 19)})
    on conflict (company, position) do nothing
    returning id`;
  return {
    method: "answerCallbackQuery", callback_query_id: callbackId,
    text: added.length ? `⭐ 보관함에 저장했습니다: ${j.company}` : `이미 보관함에 있습니다: ${j.company}`,
  };
}

// ⏰ 마감 임박 — 보관함에서 7일 안에 마감
async function urgent(chatId: number) {
  const t = today();
  const rows = await sql`
    select company, position, deadline, status from scrapped_jobs
    where deadline between ${t} and ${plusDays(t, 7)} and status not in ${sql(SKIP_STATUSES)}
    order by deadline`;
  const text = rows.length
    ? ["⏰ 7일 안에 마감되는 보관 공고", "", ...rows.map((r) =>
        `• ${r.deadline} · ${r.company} · ${r.position} (${r.status})`)].join("\n")
    : "7일 안에 마감되는 보관 공고가 없습니다.";
  return { method: "sendMessage", chat_id: chatId, text, reply_markup: MENU };
}

// 📁 보관함 — 최근 15건
async function saved(chatId: number) {
  const rows = await sql`
    select company, position, deadline, status from scrapped_jobs order by id desc limit 15`;
  const text = rows.length
    ? [`📁 보관함 (최근 ${rows.length}건)`, "", ...rows.map((r) =>
        `• [${r.status}] ${r.company} · ${r.position}${r.deadline ? ` · ~${r.deadline}` : ""}`)].join("\n")
    : "보관함이 비어 있습니다. 공고 목록에서 ⭐ 를 눌러 저장하세요.";
  return { method: "sendMessage", chat_id: chatId, text, reply_markup: MENU };
}

function help(chatId: number) {
  return {
    method: "sendMessage", chat_id: chatId, reply_markup: MENU,
    text: [
      "구직 도우미 봇입니다. 아래 버튼을 누르세요.",
      "",
      "📋 공고 더 보기 — 아직 안 본 공고 5건",
      "⏰ 마감 임박 — 보관함에서 7일 안에 마감",
      "📁 보관함 — 저장한 공고",
      "",
      "매일 아침 9시에 새 공고가 자동으로 옵니다. 공고 아래 ⭐ 를 누르면 보관함에 저장됩니다.",
    ].join("\n"),
  };
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return new Response("ok");
  const secret = await config("webhook_secret");
  if (!secret || req.headers.get("x-telegram-bot-api-secret-token") !== secret) {
    return new Response("forbidden", { status: 403 });
  }

  const update = await req.json();
  const cb = update.callback_query;
  const chatId: number | undefined = update.message?.chat?.id ?? cb?.message?.chat?.id;
  if (chatId === undefined || String(chatId) !== (await config("chat_id"))) {
    return new Response("ok"); // 내 채팅이 아니면 조용히 무시
  }

  try {
    if (cb) {
      const data: string = cb.data || "";
      if (data === "m") return json(await more(chatId));
      if (data.startsWith("s:")) return json(await save(cb.id, data.slice(2)));
      return json({ method: "answerCallbackQuery", callback_query_id: cb.id });
    }
    const text: string = update.message?.text || "";
    if (text.includes("공고 더 보기")) return json(await more(chatId));
    if (text.includes("마감 임박")) return json(await urgent(chatId));
    if (text.includes("보관함")) return json(await saved(chatId));
    return json(help(chatId));
  } catch (e) {
    console.error(e);
    return json({ method: "sendMessage", chat_id: chatId, text: `⚠️ 처리 중 오류가 났습니다: ${(e as Error).message}` });
  }
});
