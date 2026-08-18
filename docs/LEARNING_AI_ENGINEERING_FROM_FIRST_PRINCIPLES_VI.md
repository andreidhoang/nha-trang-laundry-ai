# Lộ trình làm chủ AI Engineering từ First Principles

> Gắn từng kỹ năng trong [AI Engineering Skills Map](https://charonhub.deeplearning.ai/the-ai-engineering-skills-map/)
> của Andrew Ng với **source of truth code** trong repository này.
>
> Đối tượng: kỹ sư muốn trở thành **production AI engineer** — người build được hệ thống AI
> chạy thật, với khách hàng thật, tiền thật, và chịu được audit.
>
> Cách dùng: đọc mỗi module cùng với file code được chỉ dẫn. Mở file, đọc code trước,
> đọc giải thích sau. Cuối mỗi module có câu hỏi tự kiểm tra — nếu không trả lời được,
> quay lại đọc code.

---

## Mục lục

- [Giai đoạn 0 — Hai first principles chi phối toàn bộ](#giai-đoạn-0--hai-first-principles-chi-phối-toàn-bộ)
- [Giai đoạn 1 — Software Engineering Fundamentals](#giai-đoạn-1--software-engineering-fundamentals)
- [Giai đoạn 2 — Building blocks của AI application](#giai-đoạn-2--building-blocks-cỦa-ai-application)
- [Giai đoạn 3 — Evals & Error Analysis (kỹ năng đắt giá nhất)](#giai-đoạn-3--evals--error-analysis-kỹ-năng-đắt-giá-nhất)
- [Giai đoạn 4 — Using Coding Agents](#giai-đoạn-4--using-coding-agents)
- [Giai đoạn 5 — Shaping the Build](#giai-đoạn-5--shaping-the-build)
- [Giai đoạn 6 — Checklist mastery & lộ trình 12 tuần](#giai-đoạn-6--checklist-mastery--lộ-trình-12-tuần)

---

## Giai đoạn 0 — Hai first principles chi phối toàn bộ

Mọi quyết định kiến trúc trong repo này — và trong bất kỳ hệ thống AI production nào —
đều suy ra được từ hai nguyên lý gốc. Hiểu hai cái này, bạn tự suy ra được phần còn lại.

### Nguyên lý 1: Output của AI là một **phân phối xác suất**, không phải một hàm

Khi bạn gọi `f(x)` trong code thường, cùng `x` luôn ra cùng kết quả. Khi bạn prompt một LLM,
bạn đang **lấy mẫu từ một phân phối xác suất** trên không gian văn bản. Cùng một prompt,
hai lần gọi có thể ra hai câu trả lời khác nhau. Hệ quả tất yếu:

1. Bạn **không thể kiểm chứng** (verify) một hệ thống AI bằng lập luận logic thuần túy —
   bạn phải **đo** (measure) nó bằng thống kê. Đây là lý do evals tồn tại.
2. Bạn **không thể giao quyền quyết định** cho một thứ không dự đoán được trong các miền
   cần tính đúng tuyệt đối: tiền, chính sách, SLA, quyền hạn, trạng thái đơn hàng.
3. Vậy kiến trúc đúng là: **deterministic core, probabilistic edge** — code quyết định ở
   lõi, model ở rìa làm phần việc vốn dĩ xác suất (hiểu ngôn ngữ, soạn nháp, giải thích).

Đọc: `specs/ENGINEERING_SPEC_V1.md` (nguyên tắc P-01…P-07), rồi đối chiếu câu khẩu quyết
trong `docs/PROJECT_ENGINEERING_FIRST_PRINCIPLES_VI.md`:

> "Code quyết định. AI hiểu ngôn ngữ và soạn nháp. Con người duyệt cam kết quan trọng.
> PostgreSQL lưu sự thật."

### Nguyên lý 2: Niềm tin (trust) phải được **kiếm bằng bằng chứng**, không được mặc định

Một hệ thống mới không có quyền tự động hóa. Nó leo từng nấc:
`draft → shadow → internal execute → approved external execute → bounded automation`,
và mỗi nấc đòi hỏi **evidence** (bằng chứng đo lường được, có hash, có chữ ký) chứ không
phải lời nói. Đây là lý do toàn bộ cỗ máy gate/evidence trong `delivery/` tồn tại.

Hai nguyên lý này trả lời cho mọi câu hỏi "tại sao thiết kế vậy?" trong các giai đoạn sau.

---

## Giai đoạn 1 — Software Engineering Fundamentals

> Kỹ năng #2 của Andrew Ng. Ông nói: hiểu sâu fundamentals = *nhận ra được trade-off nào
> đang tồn tại*. Người vibe-code không thấy trade-off, nên coding agent của họ chọn bừa —
> và thường chọn sai. Module này dạy bạn nhìn thấy các trade-off đó qua code thật.

### 1.1 State & single source of truth — tại sao "PostgreSQL lưu sự thật"

**First principle.** Một hệ thống vận hành tồn tại để ghi nhận sự thật về thế giới
(đơn hàng, giá đã chốt, ai đã duyệt). Nếu sự thật nằm rải rác trong CSV, tin nhắn chat,
và bộ nhớ con người, thì hệ thống *không có* — mọi automation xây trên nó là lâu đài cát.
Do đó: **một nguồn sự thật duy nhất, có giao dịch nguyên tử, có lịch sử không xóa được.**

**Đọc code theo thứ tự:**

1. `packages/db/migrations/0001_transaction_foundation.sql` — nền móng giao dịch.
   Để ý: 27 migration được apply tuần tự bởi `scripts/apply_migrations.py`, không dùng ORM.
   Tại sao không ORM? Vì schema *là* source of truth — SQL thuần đọc được chính xác những
   gì database thực thi, không có lớp ánh xạ che giấu.
2. Tìm trong các migration: bảng domain event, bảng audit, bảng outbox. Ba thứ này luôn
   đi cùng mutation trong **một transaction**. Đây là invariant 5 trong `context/INVARIANTS.md`.

**Tại sao thiết kế vậy?** Pattern *atomic mutation + domain event + audit + outbox* giải
quyết một bài toán kinh điển của distributed systems: "làm sao vừa ghi DB vừa gửi message
mà không mất một trong hai?". Câu trả lời: không gửi message lúc ghi DB — ghi *ý định gửi*
vào bảng outbox trong cùng transaction, rồi một worker đọc outbox và gửi sau. Nếu crash ở
bất kỳ điểm nào, trạng thái vẫn nhất quán. Trong repo này, chỉ outbox worker được gửi tin
ra ngoài — agent runtime **không có** credential để gửi. Một quyết định an toàn được ép
bằng kiến trúc, không phải bằng lời hứa.

**Tự kiểm tra:** Nếu API crash ngay sau khi commit nhưng trước khi trả response, khách
hàng retry — chuyện gì xảy ra? (Gợi ý: tìm idempotency key trong `packages/db` và
`apps/api/src/nha_trang_laundry_api/main.py`.)

### 1.2 Tiền là số nguyên, và mọi phép tính tiền đều để lại dấu vết

**First principle.** Số thực dấu phẩy động (float) không biểu diễn chính xác phân số thập
phân: `0.1 + 0.2 != 0.3`. Trong miền tiền tệ, sai số tích lũy = mất tiền thật + mất niềm tin.
Hai quy tắc suy ra: (a) lưu tiền dưới dạng **số nguyên ở đơn vị nhỏ nhất** (VND không có
đơn vị con, nên lưu thẳng đồng dạng `int`); (b) mọi làm tròn phải tường minh và nằm trong
một chỗ duy nhất.

**Đọc code:**

1. `packages/domain/src/nha_trang_laundry_domain/money.py` — nhỏ một cách cố ý:
   `require_non_negative_vnd(amount_vnd: int) -> int`. Toàn bộ hệ thống giao tiếp bằng
   `int` VND; không một float nào được chạm tiền.
2. `packages/domain/src/nha_trang_laundry_domain/pricing.py` — đọc kỹ bốn dataclass
   `frozen=True`: `PriceTier`, `PriceRule`, `PriceLine`, và đặc biệt **`CalculationTrace`**
   và `PriceResult`. Hàm `_price()` dùng `Decimal` cho trung gian và `_round_vnd()` ở đúng
   một chỗ.
3. Để ý `CalculationTrace`: kết quả tính giá không chỉ là một con số mà là **chuỗi các
   bước đã áp dụng** (rule nào, tier nào, làm tròn thế nào).

**Tại sao thiết kế vậy?** Vì khi khách hỏi "sao đơn này 183.000đ?", câu trả lời phải là
một *bằng chứng tái hiện được*, không phải "chắc model nói vậy". Trace cũng chính là thứ
grader đọc khi chấm eval (xem Giai đoạn 3). Frozen dataclass = kết quả tính xong không ai
sửa được nữa — immutability là một hình thức an toàn.

**Tự kiểm tra:** Tại sao `price_lines()` nhận rule từ ngoài vào thay vì tự đọc giá từ DB?
(Gợi ý: quote snapshot — giá tại thời điểm chốt phải được đóng băng vào quote, không được
đổi khi bảng giá đổi.)

### 1.3 Typed schema ở mọi biên — hợp đồng trước, implementation sau

**First principle.** Bug rẻ nhất là bug bị chặn ngay tại biên. Bên trong một hàm bạn tin
được type checker; nhưng tại biên (HTTP request, message từ channel, output của LLM,
message cho worker) dữ liệu đến từ thế giới không tin được. Vậy: **mọi biên đều có schema
tường minh, và schema được kiểm bằng máy trong CI.**

**Đọc code:**

1. `specs/contracts/` — 16 hợp đồng JSON Schema / OpenAPI. Đọc `agent-tools-v1.openapi.yaml`
   trước: 10 tool cố định mà public agent được phép gọi. Đây là *toàn bộ* khả năng tác
   động thế giới của agent — không có tool generic nào khác.
2. `packages/contracts/src/nha_trang_laundry_contracts/` — schema dạng Python cho các biên
   nội bộ: `channel_envelope.py`, `agent_runner.py`, `tool_registry.py`, `release_manifest.py`.
3. `scripts/verify_contracts.py` — chạy thử: `uv run python scripts/verify_contracts.py`.

**Tại sao thiết kế vậy?** Output của LLM là văn bản tự do — biên nguy hiểm nhất trong hệ
thống. Khi model trả về structured output, nó phải validate được bằng schema; khi model
gọi tool, arguments phải khớp OpenAPI. Biên có schema = lời hứa kiểm chứng được; biên không
schema = hy vọng. Đây cũng là lý do AGENTS.md ghi "Use typed schemas at every boundary".

### 1.4 Fail-closed — khi không chắc, quyền mặc định là "không"

**First principle.** Có hai kiểu hệ thống khi gặp tình huống không lường trước:
fail-open (cho qua, xử lý sau) và fail-closed (chặn, hỏi con người). Với hệ thống giải
trí, fail-open chấp nhận được. Với hệ thống chạm tiền/PII/quyền hạn, một lần fail-open
= sai tiền thật, gửi tin thật, lộ dữ liệu thật — không undo được. Vậy mặc định phải là
`REQUIRE_HUMAN` hoặc `NOT_SUPPORTED`.

**Đọc code:** `packages/policy/src/nha_trang_laundry_policy/decision.py` — đây là một trong
những file đáng đọc nhất repo. Theo thứ tự:

1. `PolicyOutcome` — enum kết quả. Đếm xem có bao nhiêu outcome là "cho phép" so với
   "chặn/chuyển người". Tỉ lệ đó *là* triết lý thiết kế.
2. `CapabilityPolicyRequest` / `CapabilityPolicySnapshot` / `AuthorityBinding` /
   `ObligationState` — mọi thứ cần để ra quyết định được gom thành snapshot bất biến.
3. `PolicyDecisionPoint` — đọc toàn bộ class này. Chú ý: policy engine **không gọi LLM**,
   không đọc DB tự do; nó là hàm thuần trên snapshot. Quyết định policy phải tái hiện được
   100% từ snapshot — đó là điều kiện để audit.

**Tại sao thiết kế vậy?** Nếu policy engine gọi model, bạn đã đưa thứ không dự đoán được
vào chính chỗ cần dự đoán nhất — vi phạm Nguyên lý 1. Nếu policy engine đọc DB tự do,
quyết định của nó phụ thuộc thời điểm đọc — không replay được. Snapshot bất biến + hàm
thuần = mọi quyết định policy trong quá khứ đều giải thích và tái hiện được.

### 1.5 Nhìn thấy trade-off: modular monolith, không microservices; PWA không bundler

**First principle.** Mọi lựa chọn kiến trúc là đánh đổi giữa cost, scalability,
reliability, tốc độ phát triển, và độ phức tạp vận hành. Câu hỏi đúng không phải "cái nào
tốt nhất" mà là "với ràng buộc của mình, đánh đổi nào có lợi nhất".

**Hai case study trong repo:**

1. **Modular monolith** (nguyên tắc P-06, `specs/ENGINEERING_SPEC_V1.md`): một FastAPI app
   (`apps/api`), một worker (`apps/worker`), một PostgreSQL. Không Kafka, không K8s,
   không vector DB. Ràng buộc: đội nhỏ, cần audit, cần vận hành đơn giản. Với ràng buộc đó,
   microservices mua "scalability" mà ta chưa cần, trả giá bằng độ phức tạp vận hành và
   mất tính nguyên tử của transaction — đánh đổi thua lỗ. Ranh giới module được giữ bằng
   `packages/*` trong uv workspace (`pyproject.toml`), nên nếu ngày nào đó cần tách, đường
   cắt đã có sẵn.
2. **Staff PWA không dùng bundler** (`apps/web/README.md`): vanilla ES modules, không
   `package.json`. Đọc file README này — nó ghi rõ lý do: một cây npm thứ hai sẽ **né được**
   licence scan, npm audit, và SBOM tooling đang có. Trade-off: mất DX của framework
   frontend, được chuỗi cung ứng phần mềm kiểm soát trọn vẹn. Đây là kiểu quyết định chỉ
   ai hiểu fundamentals mới *nhìn ra được* — đúng nghĩa câu của Andrew Ng.

**Tự kiểm tra:** Kể tên một trade-off mà bạn *không đồng ý* với repo này, và lập luận bằng
ràng buộc cụ thể (đội hình, traffic, đối tượng khách hàng) thay vì bằng khẩu hiệu.

---

## Giai đoạn 2 — Building blocks của AI application

> Kỹ năng #1 (phần đầu) của Andrew Ng: hiểu các khối xây dựng — LLM, context engineering,
> tools, agentic workflows — và cách lắp chúng thành hệ thống.

### 2.1 LLM thực chất là gì — và vì sao nó quyết định toàn bộ kiến trúc

**First principle.** Một LLM là mô hình dự đoán token tiếp theo, được huấn luyện trên
văn bản. Nó không "biết" giá giặt ủi; nó sinh ra chuỗi token *trông có vẻ hợp lý*. Ba hệ
quả kỹ thuật:

1. **Nó sẽ bịa (hallucinate) với vẻ tự tin.** Không có cơ chế nào bên trong model ngăn nó
   nói sai — vì vậy cơ chế ngăn phải nằm *ngoài* model, trong kiến trúc.
2. **Context là tất cả những gì nó có.** Model không có bộ nhớ, không có DB; mọi thứ nó
   "biết" trong một lượt nằm trong context window. Kỹ thuật đưa đúng dữ liệu, đúng format,
   đúng lúc vào context gọi là **context engineering** — và nó quan trọng hơn "prompt
   engineering" rất nhiều.
3. **Chi phí và độ trễ tỉ lệ với token.** Mọi thứ bạn nhét vào context đều tốn tiền và
   thời gian — nên context engineering cũng là bài toán kinh tế.

**Đọc code:** `runtime/model-registry-v1.yaml` — đọc toàn bộ, từng dòng. Đây là bản kê
khai toàn bộ "quyền lực" của model trong hệ thống:

- `candidate_status: EVAL_ONLY` — model này chưa được phép phục vụ ai cả.
- `provider_data_gate` — gắn với quyết định `DEC-006`: `real_customer_data_allowed: false`.
  Dữ liệu khách thật chưa được chạm model, bất kể code có sẵn sàng hay chưa.
- `prompt.bundle_sha256` — prompt bundle được **hash-pin**. Đổi một khoảng trắng trong
  prompt = hash khác = registry phải cập nhật có chủ đích. Prompt là code, được review
  như code, version như code.
- `required_response_store: false` + `store_false_override_verified: false` — hệ thống
  ghi rõ nó *yêu cầu* provider không lưu response, và ghi rõ yêu cầu đó **chưa được xác
  minh**. Không giả vờ đã xác minh.

**Tại sao thiết kế vậy?** Vì theo Nguyên lý 2, trust phải kiếm bằng evidence. Registry
này là một danh sách các niềm tin chưa được cấp, mỗi cái có trạng thái xác minh riêng.
Một hệ thống production-grade không nói "an toàn rồi" — nó nói chính xác *cái gì đã được
chứng minh, cái gì chưa*.

### 2.2 Agent loop có ràng buộc — agent không phải vòng lặp vô hạn

**First principle.** "Agent" = LLM trong một vòng lặp: nhận context → sinh output → có thể
gọi tool → nhận kết quả tool → lặp lại. Vòng lặp này có ba cách thất bại: (a) không bao
giờ dừng; (b) tốn tiền vô hạn; (c) gọi tool gây hại. Một agent production phải bị ràng
buộc ở **cả ba chiều: số bước, ngân sách, và quyền hạn tool** — và ràng buộc phải nằm
ngoài model, trong code.

**Đọc code:**

1. `apps/worker/src/nha_trang_laundry_worker/responses_runtime.py` — đọc cụm exception
   đầu file (`ResponsesBudgetExhausted`, `ResponsesContextRejected`,
   `ResponsesOutcomeAmbiguous`, `ResponsesTransportTimeout`…). Mỗi exception là một failure
   mode đã được liệt kê trước và xử lý riêng. Đây là bài học sâu nhất của file: **kỹ sư
   giỏi không viết happy path — họ liệt kê failure modes rồi đặt tên cho từng cái.**
2. Đọc tiếp `ResponsesRuntimeConfig` và các model `ResponsesRequest` /
   `ResponsesProviderResponse`: adapter gọi OpenAI Responses API được tự viết, tối giản,
   chỉ chứa đúng các field hệ thống dùng — không import SDK khổng lồ (đối chiếu ADR-0003
   provider-neutral trong `docs/adr/`).
3. `apps/worker/src/nha_trang_laundry_worker/agent_runner.py` — xem agent loop được bind
   với run record trong DB (migration `0010_agent_run_binding.sql`): mọi run đều là một
   dòng dữ liệu có trạng thái, không phải một tiến trình bay hơi.
4. Quay lại `runtime/model-registry-v1.yaml`: giới hạn cứng — tối đa 3 model calls, 6 tool
   calls, 8k token vào / 1.2k token ra, deadline 20 giây, $0.10/lượt.

**Tại sao thiết kế vậy?** Một Concierge trả lời khách về giặt ủi không cần 30 bước suy
luận — 3 model calls là đủ cho classify → tra tool → soạn nháp. Giới hạn chặt làm hai
việc: (a) chặn thất bại kiểu "agent chạy loạn tốn tiền" ngay cả khi prompt bị injection;
(b) ép kỹ sư thiết kế flow đơn giản — nếu 3 calls không đủ, vấn đề nằm ở thiết kế flow,
không phải ở giới hạn.

**Tự kiểm tra:** `ResponsesOutcomeAmbiguous` nghĩa là gì, và tại sao "không biết request
có thành công hay không" là một failure mode *riêng* đáng có exception riêng? (Gợi ý: đây
là bài toán exactly-once kinh điển của distributed systems — nếu retry mù, bạn có thể gọi
model hai lần và tốn tiền hai lần, hoặc gửi hai bản nháp.)

### 2.3 Tools: cách duy nhất agent chạm vào thế giới thật

**First principle.** LLM chỉ sinh văn bản. Nó "gọi tool" bằng cách sinh ra văn bản có cấu
trúc mà *runtime của bạn* đọc và thực thi. Nghĩa là: **quyền hạn của agent = tập tool bạn
expose, không hơn không kém.** Muốn agent an toàn, đừng dạy nó cẩn thận — hãy không đưa
nó tool nguy hiểm.

**Đọc code:**

1. `specs/contracts/agent-tools-v1.openapi.yaml` — 10 tool. Đọc từng tool và tự hỏi: tool
   này *đọc* hay *ghi*? Tool ghi nào cần approval trước?
2. `apps/public-agent-tools/src/nha_trang_laundry_agent_tools/facade.py` — Tool Facade:
   lớp duy nhất agent nhìn thấy. Đọc cách facade ủy quyền xuống `backend.py` và cách
   `auth.py` xác thực caller.
3. Đối chiếu danh sách "LLM may / may not" trong
   `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` (mục tương đương dòng 37–66): model được
   phân loại intent, trích field, soạn nháp, giải thích, dịch; **không bao giờ** được
   originate giá, giảm giá, trạng thái đơn, consent, refund, thông tin ngân hàng.

**Tại sao thiết kế vậy?** Facade + schema cố định biến "prompt injection lừa agent làm
điều xấu" từ một thảm họa thành một non-event: giả sử injection thành công 100%, agent
cũng chỉ gọi được 10 tool đã khai báo, các tool ghi đều qua `PolicyDecisionPoint`
(fail-closed, Giai đoạn 1.4), và chỉ outbox worker mới gửi được tin ra ngoài. Defense in
depth: không một lớp nào phải hoàn hảo.

### 2.4 Vì sao repo này KHÔNG có RAG / vector DB

Andrew Ng liệt kê RAG như một building block. Vậy tại sao một hệ thống trả lời khách hàng
lại không dùng? Đây là bài học quan trọng về **chọn công nghệ từ first principles thay vì
từ checklist hype**:

- RAG giải bài toán: "kiến thức lớn, thay đổi chậm, không nằm trong training data" →
  retrieve đoạn liên quan nhét vào context.
- Bài toán của tiệm giặt: bảng giá, promotion, SLA, zone giao hàng — dữ liệu **nhỏ, có cấu
  trúc, và cần chính xác tuyệt đối**. Dữ liệu nhỏ + có cấu trúc = query SQL qua typed tool
  chính xác hơn, rẻ hơn, audit được hơn semantic search.
- Hơn nữa, retrieve bằng embedding lại đưa một bước *xác suất* vào đường lấy dữ kiện —
  đúng chỗ cần deterministic nhất (Nguyên lý 1).

Kết luận: RAG là công cụ đúng cho corpus văn bản lớn (tài liệu, FAQ dài). Ở đây `templates/`
(15 CSV) và `packages/domain` là đủ. Khi nào vertical khác (ví dụ tư vấn quy trình dài)
cần, RAG sẽ được thêm — có chủ đích, có eval đi kèm. **Không dùng một công nghệ cũng là
một quyết định kiến trúc, và phải giải thích được.**

### 2.5 Đường đi của một tin nhắn — ghép toàn bộ building blocks

Đọc `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` (đoạn kiến trúc đầu file) rồi lần theo code:

```
tin nhắn khách
  → channel adapter (specs/CHANNEL_ADAPTER_SPEC_V1.md)
  → durable inbox (persist + dedupe TRƯỚC khi gọi model)      [packages/db]
  → agent runner (bounded loop, registry limits)              [apps/worker/agent_runner.py]
  → ConstrainedAgentRuntime / Responses adapter               [apps/worker/responses_runtime.py]
  → typed tools (10 tool cố định)                             [apps/public-agent-tools/facade.py]
  → PolicyDecisionPoint (fail-closed)                         [packages/policy/decision.py]
  → human approval (bind theo hash nội dung render)           [packages/domain/approvals.py]
  → outbox worker (nơi DUY NHẤT gửi tin)                      [apps/worker]
```

Mỗi mũi tên là một biên có schema. Mỗi hộp là một package độc lập test được. **Đây chính
là "agentic workflow" ở dạng production** — không phải một file Python gọi
`agent.run()` trong vòng lặp while.

**Bài tập:** Vẽ lại diagram này từ trí nhớ, ghi chú ở mỗi mũi tên: schema nào kiểm soát
biên đó, và điều tồi tệ nhất có thể xảy ra nếu biên đó không có schema.

---

## Giai đoạn 3 — Evals & Error Analysis (kỹ năng đắt giá nhất)

> Andrew Ng: "A core skill is knowing how to drive **disciplined evals and error analysis
> loops**." Đây là thứ phân biệt người làm demo với người làm production.

### 3.1 First principle: bạn đang ước lượng một phân phối, không phải kiểm tra một hàm

Từ Nguyên lý 1: output của AI là phân phối xác suất. Vậy câu hỏi "hệ thống có tốt không?"
không có nghĩa; câu hỏi đúng là: **"trên phân phối input thực tế, tỉ lệ output đạt từng
mức chất lượng là bao nhiêu, và các failure mode phân bố thế nào?"** Suy ra toàn bộ
methodology:

1. Cần một **tập case đại diện** cho phân phối input (không phải 5 ví dụ bạn nghĩ ra lúc
   code).
2. Cần **grader** — cách chấm điểm output một cách tái lập được (nếu không tái lập được,
   bạn không đo, bạn đang cảm nhận).
3. Cần chạy **lặp lại được** (seed cố định, version cố định) để so sánh giữa các thay đổi.
4. Sau khi đo, làm **error analysis**: gom các case fail theo nguyên nhân, sửa nguyên nhân
   lớn nhất trước. Vòng lặp đo → phân tích → sửa → đo lại chính là "evals loop" mà
   Andrew Ng nói.

### 3.2 Manifest-driven evals — eval cũng là hợp đồng

**Đọc code:** `specs/evals/eval-manifest-v1.yaml` (1.097 dòng — đọc hết, đây là tài liệu
quan trọng bậc nhất repo). Chú ý:

- **Runtime paths**: `PRIMARY` / `FALLBACK` / `DETERMINISTIC_DEGRADED`. Hệ thống định nghĩa
  trước hành vi khi model sống, khi model chết, và khi phải chạy hoàn toàn deterministic.
  Hệ quả của Nguyên lý 2: hệ thống phải *vận hành được thủ công* khi model chết — eval
  phải kiểm chứng cả đường degraded, không chỉ đường đẹp.
- **Deterministic seed** và budget limits khớp với `runtime/model-registry-v1.yaml` —
  eval chạy trong cùng ràng buộc với production, nếu không bạn đang đo một hệ thống khác.
- **Graders**: exact match, trace assertion, OpenAPI schema, invariant/safety, và rubric
  tiếng Việt 0–2 với pass = 2.
- **Release policy**: P0 suite phải 100% pass; regression suite đóng băng ≥ 200 case.

**Tại sao rubric tiếng Việt 0–2 với pass = 2?** Với chất lượng ngôn ngữ (lễ phép, tự
nhiên, đúng văn phong thương mại Việt Nam), exact match không dùng được — phải chấm theo
thang. Thang 0–2 (sai / chấp nhận được / tốt) với ngưỡng pass = điểm tối đa là một lựa
chọn có chủ đích: đối với mặt khách hàng, "chấp nhận được" chưa đủ — đây là quyết định
*product* được encode thành cấu hình eval.

### 3.3 Graders và synthetic data — đọc code

1. `packages/evals/src/nha_trang_laundry_evals/graders.py` — đọc từng grader. Với mỗi
   grader tự hỏi: nó deterministic hay xác suất? Grader nào dùng LLM để chấm (nếu có) thì
   làm sao tránh vòng luẩn quẩn "model chấm model"?
2. `packages/evals/src/nha_trang_laundry_evals/synthetic_combinatorial.py` — **đây là một
   trong những ý tưởng đẹp nhất repo**: 669 test case được *sinh tổ hợp* bằng cách gọi
   chính các deterministic engine trong `packages/domain`. Ground truth không cần con người
   dán nhãn — vì code quyết định (Nguyên lý 1), code cũng tạo được đáp án. Model chỉ bị
   chấm ở phần việc xác suất (trích xuất, diễn đạt), còn mọi con số đều đối chiếu được
   với engine.
3. `packages/evals/src/nha_trang_laundry_evals/runner.py` và `manifest.py` — cách runner
   đọc manifest và thực thi. Chạy thử:
   `uv run --package nha-trang-laundry-evals python -m nha_trang_laundry_evals.runner`.

**Bài học tổng quát cho bất kỳ hệ thống AI nào:** câu hỏi đầu tiên khi thiết kế eval là
"ground truth lấy từ đâu?". Ba nguồn theo thứ tự tốt dần: (a) con người dán nhãn — đắt,
chậm, không nhất quán; (b) LLM chấm — rẻ nhưng cần meta-eval; (c) **sinh từ deterministic
logic** — miễn phí, vô hạn, chính xác tuyệt đối, nhưng chỉ dùng được khi domain có lõi
deterministic. Repo này được thiết kế để (c) khả dĩ — một lý do nữa cho kiến trúc
deterministic core.

### 3.4 Error analysis & kỷ luật bằng chứng — phần khó nhất không nằm ở code

**Đọc:** `evidence/agent-shadow/bundle-index-v1.yaml` rồi đọc câu này trong protocol:
*"a hand-adjusted bundle is a fabricated attestation"* (một bundle chỉnh tay là một lời
chứng thực giả mạo).

Toàn bộ 32 case trong shadow bundle hiện tại đều ghi `DETERMINISTIC_DEGRADED` SKIP —
nghĩa là **model chưa từng được gọi**, và hệ thống thà thừa nhận "chưa có evidence về
model" còn hơn gán nhãn evidence tổng hợp thành evidence thật. Invariant 20 trong
`context/INVARIANTS.md` cấm relabel.

**Đây là kỹ năng mềm-cứng mà công ty AI hàng đầu săn tìm:** kỷ luật không tự đánh lừa
mình. Ai cũng viết được eval; rất ít người chịu được áp lực "đánh dấu xong cho sếp vui"
mà vẫn ghi SKIP. Error analysis vô giá trị nếu dữ liệu đầu vào là giả.

**Tự kiểm tra:** Giả sử sếp bảo "demo cho khách tuần sau, bỏ qua vài case fail đi".
Trong repo này, hành vi đó vi phạm cụ thể những cơ chế nào? (Gợi ý: hash-pin của bundle,
`required_evidence` trong `delivery/WORK_QUEUE.yaml`, invariant 20, và điều 5 trong mục
continue-execution của `AGENTS.md`.)

---

## Giai đoạn 4 — Using Coding Agents

> Kỹ năng #3 của Andrew Ng: dùng agentic coding hiệu quả — quản lý context, cân bằng
> planning/execution, cung cấp verifier để agent tự đóng vòng lặp, làm việc theo spec,
> tránh pitfall. Điểm thú vị: repo này không chỉ *dùng* coding agent — nó **cơ chế hóa**
> cách dùng thành giao thức. Học giao thức này = học kỹ năng ở dạng tinh luyện nhất.

### 4.1 First principle: coding agent cũng là một hệ thống xác suất

Coding agent là LLM + tools (đọc/ghi file, chạy lệnh) trong một vòng lặp. Mọi điều ở Giai
đoạn 2 áp dụng y hệt: output không dự đoán được → không tin được → phải có verifier ngoài
agent. Khác biệt duy nhất: ở đây "thế giới thật" mà agent chạm vào là **codebase của bạn**,
và "hành động không undo được" là sửa code sai, xóa dữ liệu, hoặc đánh dấu việc chưa làm
là đã làm.

Vậy bốn thứ cần cho agent làm việc production-grade:

1. **Spec rõ ràng** — agent phải biết "xong" nghĩa là gì, bằng tiêu chí kiểm tra được.
2. **Verifier** — cách kiểm chứng kết quả không phụ thuộc lời agent nói.
3. **Context đúng** — agent đọc đúng file liên quan, không đọc cả repo, không thiếu file
   quyết định.
4. **Ràng buộc hành động** — giới hạn những gì agent được phép làm mà không hỏi.

### 4.2 Spec: work item có acceptance check chạy được

**Đọc:** `delivery/WORK_QUEUE.yaml` — mở một work item bất kỳ. Mỗi item có: `id` ổn định,
`phase`, `dependencies`, `context_domains`, `normative_sources` (spec nào là chuẩn),
`contracts` (hợp đồng nào bị chạm), **`acceptance_checks`** (lệnh cụ thể, ví dụ
`uv run pytest packages/domain/tests/test_pricing.py`), và **`required_evidence`**.

Đây là câu trả lời của repo cho câu hỏi của Andrew Ng: "how to work with a clear spec".
Spec không phải đoạn văn mô tả — spec là **tập lệnh mà khi chạy xanh nghĩa là xong**.
Agent không phải đoán; người review không phải cãi nhau.

### 4.3 Verifier: hệ thống đóng vòng lặp thay agent

**Đọc và chạy:**

```bash
uv run pytest                      # ~711 test, gồm hypothesis property tests
uv run ruff check . && uv run mypy apps packages
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Bốn lớp verifier này cho phép agent **tự kiểm tra trước khi tuyên bố xong** — chính là
"help the agent autonomously close loops by providing verifiers". Hypothesis (property-
based testing) đáng chú ý riêng: thay vì viết từng ví dụ, bạn phát biểu tính chất bất
biến ("giá không bao giờ âm", "tổng line = tổng quote") và máy sinh hàng nghìn case —
đối thủ tự nhiên của code domain deterministic (Giai đoạn 1.2).

### 4.4 Context management: `CONTEXT_MAP` + context packet

**Đọc:** `context/CONTEXT_MAP.yaml` và `scripts/assemble_context.py`. AGENTS.md quy định:
*"Assemble a context packet from `context/CONTEXT_MAP.yaml` for any multi-file or
sensitive task."*

First principle: context window là tài nguyên hữu hạn và chất lượng output của agent suy
giảm khi context nhiễu. Vậy việc "đưa cái gì vào context" phải là **quyết định có cấu
trúc**, không phải `cat` cả repo. Context map ánh xạ domain → file normative → code →
test → evidence; khi làm task chạm domain X, bạn assemble đúng packet cho X. Đây là
context engineering (Giai đoạn 2.1) áp dụng cho chính quy trình phát triển.

### 4.5 Ràng buộc hành động: delivery loop, CAS, và automation lease

Đây là phần repo đi xa hơn bản đồ của Andrew Ng — đọc kỹ:

1. `context/CONTINUATION_PROTOCOL.md` — state machine của công việc:
   `PENDING/READY → IN_PROGRESS → COMPLETE | BLOCKED`. Luật cứng:
   - Chỉ được có **một** item `IN_PROGRESS` (`delivery/LOOP_STATE.yaml`).
   - Completion đòi hỏi evidence thật: *"A description of prior work, existing code, or a
     green generic test run is not completion evidence. Do not silently accept skipped
     required integration tests, fabricate results, weaken a check."*
   - Bị chặn (thiếu credential, policy chưa rõ, hành động phá hủy) → **fail closed**:
     ghi blocker, chuyển item độc lập khác, không tự ý phóng.
2. `scripts/run_delivery_loop.py` + `scripts/record_delivery_evidence.py` — mutation của
   delivery state dùng **generation CAS** (compare-and-swap trên digest): ai ghi state
   phải chứng minh mình đọc đúng generation mới nhất (`--expected-generation`). Đây chính
   là optimistic concurrency control của database — áp dụng cho quy trình làm việc của
   agent, để hai agent (hoặc agent + cron) không đạp lên nhau.
3. `context/AUTOMATION_PROTOCOL.md` — cho chế độ chạy tự động theo lịch (OpenClaw cron):
   phải giữ lease trên `.openclaw/state.json`, mỗi tick chỉ được thực thi **đúng một
   action** trả về từ `scripts/run_automation_tick.py`, và phải persist `begin-attempt`
   trước khi mutate.

**Tại sao thiết kế vậy?** Vì coding agent, khi được thả tự do, có các failure mode đã
biết: làm nhiều việc một lúc rồi rối state, tuyên bố xong khi chưa xong, "sửa" test cho
xanh thay vì sửa code, và retry một hành động nguy hiểm. Ba cơ chế trên chặn cả ba bằng
*cấu trúc* (một IN_PROGRESS, evidence bắt buộc, CAS + lease + một action/tick) thay vì
bằng lời dặn dò trong prompt. **Bài học: prompt là lớp phòng thủ yếu nhất; cơ chế là lớp
mạnh nhất.**

### 4.6 Các pitfall kinh điển — checklist tự soi

Andrew Ng cảnh báo "an agent messing up your production database". Tổng quát hóa từ repo
này, checklist pitfall khi vận hành coding agent:

- [ ] Agent có đường tới credential/DB production không? (Ở đây: provider key chỉ trong
  `/run/secrets` hoặc env, và **không đường code nào đọc** — `docs/runbooks/provider-credentials.md`.)
- [ ] "Xong" của agent có verifier độc lập không, hay bạn đang tin lời nó?
- [ ] Agent có thể sửa test/spec để làm check xanh không? (Ở đây bị cấm tường minh:
  "weaken a check" là vi phạm protocol.)
- [ ] Hai agent chạy song song có đạp state nhau không? (Ở đây: CAS + lease + mutex +
  write-ahead journal trong `scripts/delivery_state.py`.)
- [ ] Khi agent gặp tình huống ngoài thẩm quyền, nó dừng lại hỏi hay tự suy diễn?
  (Ở đây: fail-closed, ghi BLOCKED với lý do.)

---

## Giai đoạn 5 — Shaping the Build

> Kỹ năng #4: khi agent viết code ngày càng giỏi, giá trị của kỹ sư dịch chuyển sang
> **quyết định spec chứa gì**. Đòi hỏi product sense, hiểu business, biết khi nào MVP
> nhanh, khi nào chậm lại build cẩn thận.

### 5.1 Từ vấn đề kinh doanh đến spec — đọc chuỗi lập luận

**Đọc theo thứ tự:**

1. `docs/company/01_VISION_AND_STRATEGIC_THESIS.md` — luận điểm: SMB dịch vụ Việt Nam cần
   "Verified Operations OS" — conversational ở đầu vào, deterministic ở quyền quyết định,
   evidence-native. Giặt ủi chỉ là **vertical đầu tiên**; các primitive (pricing, quote,
   approval, consent, evidence) được thiết kế để tái dùng sang vertical khác.
2. `docs/PROJECT_ENGINEERING_FIRST_PRINCIPLES_VI.md` (1.868 dòng) — cùng luận điểm đó
   nhưng diễn giải cho độc giả Việt Nam, từ first principles. Đây là tài liệu nên đọc
   đầu tiên nếu bạn chỉ đọc một file.
3. `specs/ENGINEERING_SPEC_V1.md` — phần đầu mô tả hiện trạng: tiệm có bảng giá, promotion,
   SLA trong CSV (`templates/`) nhưng **không có hệ thống giao dịch** — không DB chuẩn,
   không state machine đơn hàng, không audit. Gap giữa "có quy tắc kinh doanh" và "có hệ
   thống thực thi quy tắc" chính là vấn đề được giải.

**Bài học shaping:** spec tốt bắt đầu bằng *phát biểu vấn đề không chứa giải pháp*. Để ý
V1 spec tuyên bố rõ nó **không** phải "AI điều hành tiệm giặt": code tính giá, nhân viên
ra quyết định thương mại, AI trích xuất/soạn nháp/bàn giao, hệ thống vẫn chạy thủ công
khi model chết. Phạm vi V1 được *cắt* bởi hiểu biết business (cái gì sai thì mất tiền/mất
khách ngay), không bởi khả năng kỹ thuật.

### 5.2 Earned autonomy — thang tin cậy là quyết định product, không phải kỹ thuật

**Đọc:** `delivery/GATE_REGISTRY.yaml` — 4 gate:
`G1_INTERNAL_SHADOW_READY → G2_PUBLIC_ASSISTED_ENTRY → G3_ASSISTED_EVIDENCE_COMPLETE →
G4_BOUNDED_CAPABILITY_ENTRY`. Đọc `required_evidence` của G2: kênh chính thức, VM runtime
cách ly, runtime-parity evidence có chữ ký, **≥ 14 ngày shadow / 100 tương tác / 30 đơn
thật**, và **zero defect** thuộc các lớp zero-tolerance (sai tiền, hành động trái quyền,
lộ dữ liệu, gửi trùng, bỏ sót suppression).

Rồi đọc `delivery/CAPABILITY_STATUS.yaml`: mọi capability (LIST_PRICE_INFO, PUBLIC_FAQ,
QUOTE_ESTIMATE, BOOKING…) đều `NOT_AUTHORIZED`, mỗi cái gắn với chuỗi gate riêng.

**Tại sao thiết kế vậy?** Vì rủi ro không phân bố đều: bot trả lời FAQ sai thì xấu hổ;
bot báo giá sai thì mất tiền; bot gửi tin nhắn marketing cho người đã từ chối consent thì
vi phạm pháp lý. Vậy **mỗi capability có chuỗi chứng nhận riêng** — đây là tư duy product
risk được encode thành YAML. Kỹ sư "shaping the build" là người ngồi với chủ doanh nghiệp
và vẽ ra bảng này: cái gì được tự động trước, cái gì không bao giờ tự động.

### 5.3 Decision registry — ranh giới giữa việc agent làm và việc con người quyết

**Đọc:** `context/DECISION_REGISTRY.yaml` — DEC-001…DEC-010, tất cả `OPEN`. Ví dụ:
DEC-005 (kênh chính thức — ADR-0005 đã chọn Zalo OA), DEC-006 (provider được dùng/lưu dữ
liệu thế nào), DEC-008 (lịch retention), DEC-009 (media của khách có được chạm model —
mặc định fail-closed `NOT_SUPPORTED`).

Đây là bản thể hiện rõ nhất của "shaping the build": có những quyết định **không thể suy
ra từ code** — chúng đòi hỏi thẩm quyền kinh doanh, pháp lý, và khẩu vị rủi ro của chủ
doanh nghiệp. Một hệ thống trưởng thành không để agent (hay kỹ sư) tự tiện trả lời; nó
liệt kê các quyết định đó thành registry, đánh dấu OPEN, và **fail-closed mọi đường dẫn
phụ thuộc chúng**. `docs/PATH_TO_PRODUCTION_REVIEW.md` ước tính 9 trong 10 decision đứng
giữa hiện tại và production, khoảng cách thực tế tới G2 là 24–30 tuần — con số đó là
kết quả của shaping có kỷ luật, không phải sự chậm chạp.

### 5.4 Khi nào nhanh, khi nào chậm

Andrew Ng: "knowing when to quickly build an MVP... and when to slow down and build more
carefully." Repo này là case study cho phía "chậm" — nhưng hãy hiểu *tại sao* chậm là
đúng ở đây, để bạn áp dụng đúng chỗ khác:

| Chiều | Hệ quả khi sai | Chiến lược đúng |
|---|---|---|
| Tính năng nội bộ, staff-facing, dễ revert | Nhân viên bực mình, sửa lại trong ngày | Nhanh, ship sớm, học từ usage |
| Báo giá, promotion, SLA | Mất tiền thật, mất niềm tin khách | Chậm, deterministic, snapshot, audit |
| Kênh public, tự động gửi | Pháp lý, spam, thương hiệu | Rất chậm: gate + chữ ký + zero-tolerance defect |
| Demo/prototype để học | Không hệ quả | Rất nhanh — nhưng **không được relabel thành evidence** (invariant 20) |

Nguyên tắc rút ra: **tốc độ tỉ lệ nghịch với chi phí của sai lầm và tính không đảo ngược
của hành động.** Người junior chọn một tốc độ cho mọi thứ; người senior thay đổi tốc độ
theo chi phí sai lầm của từng lớp.

### 5.5 Đọc ADR — code là hiện tại, ADR là lý do

**Đọc:** `docs/adr/` — 8 ADR. Mỗi ADR trả lời "tại sao" cho một quyết định lớn: 0001
(control plane Python), 0002 (trust boundaries), 0003 (provider-neutral runtime), 0004
(đóng băng OpenClaw làm comparator offline), 0005 (chọn Zalo OA), 0006 (release cần hai
bên ký), 0007 (topology 3 zone / 2 host), 0008 (tiered inference / multimodal — proposed).

Thói quen của production AI engineer: khi vào codebase mới, đọc ADR *trước* khi đọc code.
Code cho bạn biết hệ thống là gì; ADR cho bạn biết nó **đã từng có thể là gì khác, và vì
sao không** — đó chính là trade-off mà Andrew Ng nói bạn phải học nhìn thấy.

---

## Giai đoạn 6 — Checklist mastery & lộ trình 12 tuần

### Checklist: bạn đã là production AI engineer khi…

**Fundamentals**
- [ ] Giải thích được tại sao `0.1 + 0.2 != 0.3` và chỉ ra mọi chỗ trong repo này tiền
  được xử lý thế nào (`money.py`, `pricing.py::_round_vnd`).
- [ ] Vẽ được outbox pattern từ trí nhớ và giải thích tại sao nó thay thế "ghi DB rồi gửi
  message".
- [ ] Phát biểu được ba trade-off cụ thể của repo này (monolith, no-ORM, no-bundler) bằng
  ngôn ngữ ràng buộc, không bằng khẩu hiệu.

**AI building blocks**
- [ ] Giải thích được tại sao LLM không bao giờ được originate giá trong hệ thống này, ở
  cả ba tầng: nguyên lý xác suất, cơ chế code (policy + facade), và cơ chế quy trình
  (registry + gate).
- [ ] Liệt kê được các ràng buộc của agent runtime (3 model calls, 6 tool calls, 8k/1.2k
  token, 20s, $0.10) và lập luận được tại sao *giảm* chúng có thể làm hệ thống tốt hơn.
- [ ] Giải thích được khi nào cần RAG, và tại sao ở đây typed tool + SQL thắng.

**Evals**
- [ ] Thiết kế được một eval suite từ đầu: ground truth từ đâu, grader nào, runtime path
  nào, release policy ra sao — theo khuôn `eval-manifest-v1.yaml`.
- [ ] Chạy được eval runner, đọc được kết quả, và thực hiện một vòng error analysis thật
  (gom failure theo nguyên nhân, đề xuất fix ưu tiên).
- [ ] Nói không được với việc relabel evidence — và giải thích được tại sao bằng
  invariant 20.

**Coding agents**
- [ ] Vận hành được delivery loop: đọc `LOOP_STATE.yaml` → chạy drift check →
  `run_delivery_loop.py` → resume đúng item → chạy acceptance checks →
  `record_delivery_evidence.py --expected-generation <sha>`.
- [ ] Giải thích được generation CAS giải quyết vấn đề gì.
- [ ] Kể được năm pitfall của coding agent và cơ chế repo này dùng để chặn từng cái.

**Shaping the build**
- [ ] Thuyết trình được trong 10 phút: vì sao hệ thống này đáng làm chậm 24–30 tuần thay
  vì ship bot Zalo trong một cuối tuần — và khi nào lựa chọn ngược lại là đúng.
- [ ] Đọc một vertical mới (ví dụ: spa, sửa chữa điện nước) và phác thảo được: phần nào
  của kernel tái dùng được, decision nào phải mở lại, gate nào giữ nguyên.

### Lộ trình 12 tuần gợi ý

| Tuần | Nội dung | Vật liệu trong repo |
|---|---|---|
| 1–2 | First principles + fundamentals: state, money, schema, fail-closed | Giai đoạn 0–1; đọc `pricing.py`, `decision.py`, migrations |
| 3–4 | AI building blocks: LLM, context, tools, bounded agent loop | Giai đoạn 2; đọc `responses_runtime.py`, `facade.py`, registry |
| 5–7 | Evals chuyên sâu: chạy runner, đọc graders, tự viết một suite nhỏ | Giai đoạn 3; `packages/evals/`, `specs/evals/` |
| 8–9 | Coding agent operations: chạy delivery loop thật trên một work item READY | Giai đoạn 4; `context/`, `delivery/` |
| 10 | Shaping: đọc bộ `docs/company/`, viết lại luận điểm bằng lời mình | Giai đoạn 5 |
| 11 | Tổng hợp: chọn một capability, trình bày đường đi của nó qua các gate | `GATE_REGISTRY.yaml`, `CAPABILITY_STATUS.yaml` |
| 12 | Bài kiểm cuối: checklist mastery ở trên, tự chấm, phần nào yếu quay lại tuần tương ứng | Toàn bộ |

### Nguyên tắc xuyên suốt: học liên tục

Andrew Ng kết bài bằng "a mindset of continuous learning". Trong repo này nó hiện hữu ở
`docs/adr/0008` (tiered inference / multimodal — vẫn `proposed`) và ở chính cấu trúc
registry: model ref, prompt bundle, tool contract đều hash-pinned nên **thay đổi là việc
bình thường, được làm có chủ đích**. Hệ thống không giả định công nghệ hôm nay là cuối
cùng — nó giả định công nghệ sẽ đổi, và xây sẵn đường để đổi an toàn. Đó cũng là tư duy
bạn cần: học công cụ mới nhanh, nhưng neo mọi thứ vào hai nguyên lý không đổi ở Giai đoạn 0.

---

*Tài liệu này tham chiếu code tại thời điểm viết (2026-08). Nếu code đổi, file path có
thể lệch — khi đó chính việc tự tìm lại file đúng là bài tập đầu tiên của bạn.*
