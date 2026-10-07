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

// ---------------------------------------------------------------------------
// 보관함 관리: 목록 → 번호 누르면 그 공고 → 상태 변경·자소서 부탁·삭제
// 버튼으로 누른 건 같은 메시지를 고쳐서(editMessageText) 화면이 쌓이지 않게 한다.
// ---------------------------------------------------------------------------

// config.APPLICATION_STATUSES 와 같은 순서 (callback 에는 번호만 싣는다)
const STATUSES = ["관심", "지원 예정", "지원 완료", "서류 합격", "면접 진행", "최종 합격", "불합격"];
const REQUEST_TAG = "[자소서 요청]";
const LIST_SIZE = 10;

type View = { text: string; reply_markup?: unknown };
type Where = { chatId: number; messageId?: number }; // messageId 가 있으면 그 메시지를 고친다

function show(where: Where, view: View) {
  const base = { chat_id: where.chatId, disable_web_page_preview: true, ...view };
  return where.messageId
    ? { method: "editMessageText", message_id: where.messageId, ...base }
    : { method: "sendMessage", ...base };
}

function numberButtons(ids: number[], back: string) {
  const rows = [];
  for (let i = 0; i < ids.length; i += 5) {
    rows.push(ids.slice(i, i + 5).map((id, k) => ({ text: `${i + k + 1}`, callback_data: `j:${id}:${back}` })));
  }
  return rows;
}

// kind: "l" = 보관함 최근, "u" = 7일 안에 마감
async function listView(kind: string): Promise<View> {
  const t = today();
  const rows = kind === "u"
    ? await sql`
        select id, company, position, deadline, status from scrapped_jobs
        where deadline between ${t} and ${plusDays(t, 7)} and status not in ${sql(SKIP_STATUSES)}
        order by deadline limit ${LIST_SIZE}`
    : await sql`
        select id, company, position, deadline, status from scrapped_jobs order by id desc limit ${LIST_SIZE}`;

  if (rows.length === 0) {
    return {
      text: kind === "u"
        ? "7일 안에 마감되는 보관 공고가 없습니다."
        : "보관함이 비어 있습니다. 공고 목록에서 ⭐ 를 눌러 저장하세요.",
    };
  }

  const lines: string[] = [];
  if (kind === "u") {
    lines.push("⏰ 7일 안에 마감되는 보관 공고", "");
  } else {
    const counts = await sql`select status, count(*)::int as n from scrapped_jobs group by status`;
    const summary = STATUSES.map((s) => {
      const c = counts.find((r) => r.status === s);
      return c ? `${s} ${c.n}` : "";
    }).filter(Boolean).join(" · ");
    lines.push(`📁 보관함 (${summary})`, "");
  }
  rows.forEach((r, i) => {
    lines.push(`${i + 1}. [${r.status}] ${r.company}`, `   ${r.position}${r.deadline ? ` · ~${r.deadline}` : ""}`);
  });
  lines.push("", "번호를 누르면 상태를 바꾸거나 자소서를 부탁할 수 있습니다.");
  return { text: lines.join("\n"), reply_markup: { inline_keyboard: numberButtons(rows.map((r) => r.id), kind) } };
}

async function jobView(id: number, back: string, note = ""): Promise<View> {
  const rows = await sql`select * from scrapped_jobs where id = ${id}`;
  if (rows.length === 0) return listView(back);
  const j = rows[0];
  const requested = (j.memo || "").includes(REQUEST_TAG);
  const statusRows = [];
  for (let i = 0; i < STATUSES.length; i += 4) {
    statusRows.push(STATUSES.slice(i, i + 4).map((s, k) => ({
      text: s === j.status ? `● ${s}` : s,
      callback_data: `t:${id}:${i + k}:${back}`,
    })));
  }
  return {
    text: [
      note,
      `🏢 ${j.company}`,
      `${j.position}`,
      `상태: ${j.status}${j.deadline ? ` · 마감 ${j.deadline}` : ""}`,
      requested ? "✍️ 자소서 요청됨 — Claude Code에서 /apply 하면 이 공고부터 씁니다" : "",
      j.link || "",
    ].filter(Boolean).join("\n"),
    reply_markup: {
      inline_keyboard: [
        ...statusRows,
        [
          { text: requested ? "✍️ 요청 취소" : "✍️ 자소서 부탁", callback_data: `w:${id}:${back}` },
          { text: "🗑 삭제", callback_data: `d:${id}:${back}` },
          { text: "← 목록", callback_data: `${back}` },
        ],
      ],
    },
  };
}

async function setStatus(id: number, idx: number, back: string) {
  const status = STATUSES[idx];
  if (!status) return jobView(id, back);
  // '지원 완료' 로 처음 바뀔 때 지원일을 남긴다 (앱과 같은 규칙)
  await sql`
    update scrapped_jobs set status = ${status},
      applied_at = case when ${status} = '지원 완료' and coalesce(applied_at, '') = '' then ${today()} else applied_at end
    where id = ${id}`;
  return jobView(id, back, `✅ '${status}' 로 바꿨습니다`);
}

async function toggleRequest(id: number, back: string) {
  const rows = await sql`select memo, status from scrapped_jobs where id = ${id}`;
  if (rows.length === 0) return listView(back);
  const memo: string = rows[0].memo || "";
  if (memo.includes(REQUEST_TAG)) {
    await sql`update scrapped_jobs set memo = ${memo.replace(REQUEST_TAG, "").trim()} where id = ${id}`;
    return jobView(id, back, "자소서 요청을 취소했습니다");
  }
  const status = rows[0].status === "관심" ? "지원 예정" : rows[0].status;
  await sql`update scrapped_jobs set memo = ${`${REQUEST_TAG} ${memo}`.trim()}, status = ${status} where id = ${id}`;
  return jobView(id, back, "✍️ 자소서를 부탁했습니다");
}

async function confirmDelete(id: number, back: string): Promise<View> {
  const rows = await sql`select company, position from scrapped_jobs where id = ${id}`;
  if (rows.length === 0) return listView(back);
  return {
    text: `🗑 보관함에서 지울까요?\n\n${rows[0].company}\n${rows[0].position}\n\n저장한 자소서는 지워지지 않습니다.`,
    reply_markup: { inline_keyboard: [[
      { text: "지우기", callback_data: `D:${id}:${back}` },
      { text: "취소", callback_data: `j:${id}:${back}` },
    ]] },
  };
}

async function remove(id: number, back: string) {
  await sql`delete from scrapped_jobs where id = ${id}`;
  return listView(back);
}

function help(chatId: number) {
  return {
    method: "sendMessage", chat_id: chatId, reply_markup: MENU,
    text: [
      "구직 도우미 봇입니다. 아래 버튼을 누르세요.",
      "",
      "📋 공고 더 보기 — 아직 안 본 공고 5건 (⭐ 로 보관함 저장)",
      "⏰ 마감 임박 — 보관함에서 7일 안에 마감",
      "📁 보관함 — 지원 현황, 번호를 눌러 상태 변경·자소서 부탁·삭제",
      "",
      "매일 아침 9시에 새 공고가 자동으로 옵니다.",
      "대기업 공채 캘린더·회사별 연봉 정보는 operator24hr.com 에서 볼 수 있습니다 (출처: Operator24hr).",
    ].join("\n"),
  };
}

async function onButton(cb: { id: string; data?: string; message: { message_id: number } }, chatId: number) {
  const where: Where = { chatId, messageId: cb.message.message_id };
  const [op, a, b, c] = (cb.data || "").split(":");
  // s:<job_key> 의 키에는 ':' 가 들어 있다 (saramin:123)
  if (op === "s") return save(cb.id, (cb.data || "").slice(2));
  if (op === "m") return more(chatId);
  if (op === "l" || op === "u") return show(where, await listView(op));
  if (op === "j") return show(where, await jobView(Number(a), b));
  if (op === "t") return show(where, await setStatus(Number(a), Number(b), c));
  if (op === "w") return show(where, await toggleRequest(Number(a), b));
  if (op === "d") return show(where, await confirmDelete(Number(a), b));
  if (op === "D") return show(where, await remove(Number(a), b));
  return { method: "answerCallbackQuery", callback_query_id: cb.id };
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
    if (cb) return json(await onButton(cb, chatId));
    const text: string = update.message?.text || "";
    if (text.includes("공고 더 보기")) return json(await more(chatId));
    if (text.includes("마감 임박")) return json(show({ chatId }, await listView("u")));
    if (text.includes("보관함")) return json(show({ chatId }, await listView("l")));
    return json(help(chatId));
  } catch (e) {
    console.error(e);
    return json({ method: "sendMessage", chat_id: chatId, text: `⚠️ 처리 중 오류가 났습니다: ${(e as Error).message}` });
  }
});
