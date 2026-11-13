/**
 * 상품 목록(config/keywords.json) 보기·저장 — Vercel 서버 함수 (/api/products)
 *
 * GET  /api/products                → { ready, products, sha }  (목록은 공개 저장소에 있는 것과 같아요)
 * POST /api/products  { password, sha, products, collect }
 *      → GitHub에 keywords.json 을 커밋하고, collect 가 true 면 수집(Actions)을 바로 실행해요.
 *
 * Vercel 환경변수 (사용자가 직접 넣어요, 값은 코드·채팅에 절대 안 적어요)
 *   GITHUB_TOKEN   fine-grained 토큰: 이 저장소만, Contents 읽기/쓰기 + Actions 읽기/쓰기
 *   ADMIN_PASSWORD 상품 관리 화면 비밀번호
 *   GITHUB_REPO    (선택) 기본 djmonnar/chiikawa-price
 *
 * GitHub Pages(정적)에서는 이 파일이 그냥 글자로만 보이고 실행되지 않아요. 비밀값은 들어 있지 않아요.
 */
const crypto = require("crypto");

const PATH = "config/keywords.json";
const BRANCH = "main";
const WORKFLOW = "collect.yml";
const MAX_PRODUCTS = 40;

const repo = () => (process.env.GITHUB_REPO || "djmonnar/chiikawa-price").trim();
const token = () => (process.env.GITHUB_TOKEN || "").trim();
const adminPassword = () => process.env.ADMIN_PASSWORD || "";

async function gh(path, init = {}) {
  const r = await fetch(`https://api.github.com/repos/${repo()}${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token()}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "chiikawa-price-manager",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  const text = await r.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { message: text.slice(0, 200) };
  }
  return { status: r.status, body };
}

async function readList() {
  const { status, body } = await gh(`/contents/${PATH}?ref=${BRANCH}`);
  if (status !== 200) throw httpError(status, body);
  const products = JSON.parse(Buffer.from(body.content, "base64").toString("utf8"));
  return { products, sha: body.sha };
}

function httpError(status, body) {
  const e = new Error((body && body.message) || `GitHub HTTP ${status}`);
  e.status = status;
  return e;
}

/** 비밀번호 비교: 길이가 달라도 시간이 같게 해시로 비교 */
function passwordOk(given) {
  const want = adminPassword();
  if (!want || typeof given !== "string") return false;
  const a = crypto.createHash("sha256").update(given).digest();
  const b = crypto.createHash("sha256").update(want).digest();
  return crypto.timingSafeEqual(a, b);
}

// ------------------------------------------------------------ 입력 정리·검사
const str = (v, max) => (typeof v === "string" ? v.trim().slice(0, max) : "");
const words = (v, maxItems = 30, maxLen = 60) =>
  (Array.isArray(v) ? v : [])
    .map((w) => str(w, maxLen))
    .filter(Boolean)
    .filter((w, i, a) => a.indexOf(w) === i)
    .slice(0, maxItems);
const rows = (v) =>
  (Array.isArray(v) ? v : [])
    .map((row) => words(Array.isArray(row) ? row : [row], 20, 40))
    .filter((row) => row.length)
    .slice(0, 6);
const money = (v) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) && n > 0 && n < 100000000 ? n : null;
};

/** keywords.json 한 칸으로 정리. 모르는 칸은 버리고, 빈 칸은 안 적어요 (collect.py 가 기본값을 써요). */
function cleanProduct(p, i) {
  if (!p || typeof p !== "object") throw bad(`${i + 1}번째 상품이 비어 있어요`);
  const id = str(p.id, 40).toLowerCase();
  if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(id)) throw bad(`${i + 1}번째 상품의 id가 올바르지 않아요`);
  const name = str(p.name, 40);
  if (!name) throw bad(`${i + 1}번째 상품 이름을 적어 주세요`);
  const ko = words(p.ko, 3, 60);
  const ja = words(p.ja, 3, 60);
  if (!ko.length && !ja.length) throw bad(`‘${name}’ 검색어를 하나 이상 적어 주세요`);
  const out = { id, name, ko, ja };
  const google_q = str(p.google_q, 60);
  const google_q_ja = str(p.google_q_ja, 60);
  if (google_q) out.google_q = google_q;
  if (google_q_ja) out.google_q_ja = google_q_ja;
  const lists = {
    must_ko: rows(p.must_ko),
    must_ja: rows(p.must_ja),
    exclude_ko: words(p.exclude_ko, 40, 30),
    exclude_ja: words(p.exclude_ja, 40, 30),
  };
  for (const [k, v] of Object.entries(lists)) if (v.length) out[k] = v;
  const target = money(p.target_krw);
  const sell = money(p.sell_price);
  if (target) out.target_krw = target;
  if (sell) out.sell_price = sell;
  return out;
}

function bad(message) {
  const e = new Error(message);
  e.status = 400;
  return e;
}

function cleanList(list) {
  if (!Array.isArray(list)) throw bad("상품 목록이 비어 있어요");
  if (list.length > MAX_PRODUCTS) throw bad(`상품은 ${MAX_PRODUCTS}개까지예요`);
  const out = list.map(cleanProduct);
  const ids = new Set();
  for (const p of out) {
    if (ids.has(p.id)) throw bad(`같은 id가 두 번 있어요: ${p.id}`);
    ids.add(p.id);
  }
  return out;
}

/** 칸 순서와 상관없이 같은지 비교 */
const canon = (v) =>
  Array.isArray(v)
    ? `[${v.map(canon).join(",")}]`
    : v && typeof v === "object"
      ? `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(",")}}`
      : JSON.stringify(v);

/** 커밋 메시지: 무엇이 바뀌었는지 한 줄로 */
function summary(before, after) {
  const old = new Map(before.map((p) => [p.id, p]));
  const now = new Map(after.map((p) => [p.id, p]));
  const added = after.filter((p) => !old.has(p.id)).map((p) => `+${p.name}`);
  const removed = before.filter((p) => !now.has(p.id)).map((p) => `-${p.name}`);
  const changed = after
    .filter((p) => old.has(p.id) && canon(old.get(p.id)) !== canon(p))
    .map((p) => `~${p.name}`);
  const moved =
    !added.length && !removed.length && !changed.length &&
    before.map((p) => p.id).join() !== after.map((p) => p.id).join();
  const parts = [...added, ...removed, ...changed];
  return parts.length ? parts.join(" ").slice(0, 120) : moved ? "순서 바꿈" : "변경 없음";
}

function bodyOf(req) {
  if (req.body && typeof req.body === "object") return req.body;
  try {
    return JSON.parse(req.body || "{}");
  } catch {
    return {};
  }
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const send = (status, data) => res.status(status).json(data);
  const ready = { token: Boolean(token()), password: Boolean(adminPassword()) };

  try {
    if (req.method === "GET") {
      if (!ready.token) return send(200, { ready });
      const { products, sha } = await readList();
      return send(200, { ready, products, sha });
    }

    if (req.method !== "POST") {
      res.setHeader("Allow", "GET, POST");
      return send(405, { error: "GET 또는 POST만 돼요" });
    }
    if (!ready.token || !ready.password) {
      return send(503, { ready, error: "아직 설정 전이에요. Vercel 환경변수 GITHUB_TOKEN·ADMIN_PASSWORD 를 넣어 주세요." });
    }

    const body = bodyOf(req);
    if (!passwordOk(body.password)) {
      await wait(800); // 마구 넣어 보기 막기
      return send(401, { error: "비밀번호가 틀렸어요" });
    }

    // 비밀번호 확인만 (화면에서 ‘확인’ 눌렀을 때)
    if (body.check) return send(200, { ok: true });

    const products = cleanList(body.products);
    if (!products.length) throw bad("상품이 하나는 있어야 해요");

    const current = await readList();
    if (body.sha && body.sha !== current.sha) {
      return send(409, { error: "그사이 다른 곳에서 목록이 바뀌었어요. 새로고침한 뒤 다시 해 주세요." });
    }

    const what = summary(current.products, products);
    if (what === "변경 없음") return send(200, { ok: true, message: what, sha: current.sha, products, collect: null });
    const message = `상품 목록 수정 (사이트): ${what}`;
    const text = JSON.stringify(products, null, 2) + "\n";
    const put = await gh(`/contents/${PATH}`, {
      method: "PUT",
      body: JSON.stringify({
        message,
        content: Buffer.from(text, "utf8").toString("base64"),
        sha: current.sha,
        branch: BRANCH,
      }),
    });
    if (put.status === 409 || put.status === 422) {
      return send(409, { error: "그사이 다른 곳에서 목록이 바뀌었어요. 새로고침한 뒤 다시 해 주세요." });
    }
    if (put.status !== 200 && put.status !== 201) throw httpError(put.status, put.body);

    let collect = null;
    if (body.collect) {
      const run = await gh(`/actions/workflows/${WORKFLOW}/dispatches`, {
        method: "POST",
        body: JSON.stringify({ ref: BRANCH }),
      });
      collect = run.status === 204 ? "started" : `failed:${run.status}`;
    }

    return send(200, {
      ok: true,
      message,
      sha: put.body && put.body.content && put.body.content.sha,
      commit: put.body && put.body.commit && put.body.commit.sha,
      products,
      collect,
    });
  } catch (e) {
    const status = e.status === 400 ? 400 : 502;
    const hint =
      e.status === 401 || e.status === 403
        ? " (GITHUB_TOKEN 권한을 확인해 주세요: 이 저장소 Contents·Actions 읽기/쓰기)"
        : e.status === 404
          ? " (저장소나 파일을 못 찾았어요. 토큰에 이 저장소가 선택돼 있는지 확인해 주세요)"
          : "";
    return send(status, { error: `${e.message}${hint}` });
  }
};

// 테스트용
module.exports._internal = { cleanList, cleanProduct, summary, passwordOk };
