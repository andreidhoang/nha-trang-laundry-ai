# Research Evidence Register

**Status:** Research record supporting strategy; not normative product or legal authority

**Research cutoff:** 2026-08-10
**Proposed owner:** Research/Product

## Evidence standard

This register distinguishes source evidence from company inference. Priority is:

1. Official law, regulator, government statistics, standards, and primary research.
2. Official technical documentation and peer-reviewed research.
3. Company engineering/product publications, clearly labeled self-reported.
4. Secondary analysis for discovery only.

Market and vendor numbers may use different dates, samples, definitions, and incentives. They should not be added together without reconciliation. A source supports a decision input; it does not prove product-market fit.

## Internal repository evidence

| Source | What it establishes | Authority/limitation |
| --- | --- | --- |
| `BUILD_ENGINEERING_SPEC.md` | Current constrained architecture, one concierge, bounded tools, deterministic authority, release stages | Governing engineering specification |
| `AGENTS.md` | Non-negotiables, context assembly, verification, continuation and handoff rules | Repository operating instruction |
| `context/INVARIANTS.md` | Cross-domain safety, runtime, channel, transaction, evidence, and policy invariants | Normative context source |
| `context/DECISION_REGISTRY.yaml` | Resolved and open product/runtime decisions | Machine-readable current decision source |
| `context/CONTEXT_MAP.yaml` | Required source selection for sensitive/multifile work | Machine-readable context routing |
| `specs/contracts/` | Typed boundaries, provider/release/tool/container/supply-chain contracts | Normative machine-readable contracts |
| `specs/evals/` | Evaluation manifest and release evidence requirements | Normative machine-readable evaluation source |
| `delivery/WORK_QUEUE.yaml` and `delivery/LOOP_STATE.yaml` | Current implementation work and state | Machine-readable delivery truth |
| `docs/PROJECT_ENGINEERING_FIRST_PRINCIPLES_VI.md` | Vietnamese first-principles framing | Approved project design context |
| `docs/adr/0002-*` and `docs/adr/0003-*` | Runtime/framework decisions and evidence status | Architecture decisions; inspect current files |

## Vietnam market and adoption evidence

| Evidence | Source | Use in strategy | Limitation |
| --- | --- | --- | --- |
| More than 930,000 operating businesses and more than five million business households were reported in late 2024 | [Vietnam Ministry of Planning and Investment](https://mpi.gov.vn/en/Pages/2024-10-8/Entrepreneurs-businesses-must-grow-stronger-to-buisrbv2i.aspx) | Establishes broad addressable operational base | Definitions/date may differ from current active/payable market |
| SMEs represent about 98% of Vietnamese enterprises (about 911,000 cited) | [Vietnam Ministry of Planning and Investment](https://mpi.gov.vn/portal/Pages/2024-8-28/Bo-truong-Nguyen-Chi-Dung-doanh-nghiep-nho-va-vua-lyv5l6.aspx) | Supports SME focus | Does not establish AI readiness or budget |
| 75.1% of newly registered enterprises in January 2025 were in services | [General Statistics Office of Vietnam](https://www.gso.gov.vn/tin-tuc-thong-ke/2025/02/buc-tranh-dang-ky-doanh-nghiep-thang-01-2025/) | Directional support for service-workflow wedge | One month of registrations, not the stock of firms or buying demand |
| More than 90% of SMEs reportedly used at least one digital application by 2024 while systematic AI use remained limited | [Vietnam Ministry of Information and Communications](https://beta-en.mic.gov.vn/ai-and-its-impact-on-dual-transformation-for-vietnamese-smes-197251130205252421.htm) | Suggests digital access but an AI execution gap | Article-level aggregate; definition/method should be checked before investment claims |
| AWS survey of 1,000 Vietnam business leaders reported 18% AI adoption and mostly basic uses; skills were a barrier | [AWS press release, 2025](https://press.aboutamazon.com/sg/aws/2025/9/new-aws-research-shows-strong-ai-adoption-momentum-in-vietnam) | Signals interest and adoption constraints | Vendor-sponsored, self-reported, sampling/definition caveats; not independent proof |
| Vietnam digital economy was estimated at USD 39B in 2025 and digital payments at USD 178B GTV | [Google/Temasek/Bain e-Conomy SEA Vietnam 2025 report](https://services.google.com/fh/files/misc/vietnam_e_conomy_sea_2025_report.pdf) | Indicates digital transaction/channel scale | Industry estimate; broad economy, not target workflow TAM |
| Vietnam e-commerce exceeded USD 25B in 2024 | [Ministry of Industry and Trade](https://moit.gov.vn/khoa-hoc-va-cong-nghe/thuong-mai-dien-tu-viet-nam-nam-2024-nhung-buoc-tien-va-thach-thuc.html) | Supports digital commerce/integration context | Not direct demand for agentic operations |
| A Vietnam SME ERP experiment saw usage fall from about 80% after training to about 40%, then 35% after 18 months | [World Bank case study](https://blogs.worldbank.org/en/allaboutfinance/challenges-with-digital-technology-adoption-by-smes--a-case-stud) | Makes champion, immediate value, and sustained adoption first-class product concerns | Study context and intervention may not generalize to this product |
| Khanh Hoa reported 1,441 accommodations, more than 70,500 rooms, and 111 three-to-five-star properties with over 28,000 rooms | [Khanh Hoa News, July 2025](https://news.baokhanhhoa.vn/tourism/202507/7-months-over-105m-tourist-arrivals-to-khanh-hoa-province-5a448d5/) | Supports local hospitality-adjacent discovery | Regional tourism supply, not validated buyer demand |

## Vietnam channel evidence

| Evidence | Source | Use in strategy | Limitation |
| --- | --- | --- | --- |
| Zalo reports 78.3M monthly active users and 2B daily messages in H1 2025 | [Zalo product page](https://zalo.me/en/product/zalo) | Supports Zalo as an important customer channel | Company-reported; consumer reach does not equal OA workflow eligibility |
| Official Zalo OA OpenAPI exists | [Zalo OA OpenAPI](https://oa.zalo.me/home/function/extension?type=open-api) | Supports official connector strategy | Actual scopes, review, quotas, and terms must be verified per implementation |
| Official Zalo OA pricing document dated for 2026 | [Zalo Cloud pricing PDF](https://content.zalo.cloud/uploads/ZAP_Bang_gia_Dich_vu_ZOA_01062026_esigned_f340eb1ceb.pdf) | Input to connector economics and monitoring | Pricing can change; effective terms and account class govern |
| Anti-spam/advertising rules include consent and messaging constraints | [Decree 91/2020/ND-CP](https://vanban.chinhphu.vn/default.aspx?docid=200773&pageid=27160) | Supports deterministic consent/suppression controls | Counsel must determine applicability to exact message/workflow |

## Vietnam legal and regulatory evidence

| Instrument | Official source | Effective date noted | Strategy impact |
| --- | --- | --- | --- |
| Law on Personal Data Protection No. 91/2025/QH15 | [Government legal database](https://vanban.chinhphu.vn/?docid=214590&pageid=27160) | 2026-01-01 | Data inventory, purpose, rights, processing/transfer/provider assessment |
| Law on Data | [Government policy portal full text](https://xaydungchinhsach.chinhphu.vn/toan-van-luat-du-lieu-119250226145839949.htm) | 2025-07-01 | Data governance and applicable data obligations |
| Law on Artificial Intelligence No. 134/2025/QH15 | [Official Gazette](https://congbao.chinhphu.vn/van-ban/luat-so-134-2025-qh15-468694.htm) and [government summary](https://xaydungchinhsach.chinhphu.vn/nhung-noi-dung-dang-chu-y-cua-luat-tri-tue-nhan-tao-119260212091614393.htm) | 2026-03-01 | AI inventory, risk, transparency/oversight, evidence and legal review |
| High-risk AI system list, Decision No. 33/2026/QD-TTg | [Government legal database](https://chinhphu.vn/?classid=1&docid=218658&pageid=27160&typegroupid=5) | 2026-08-15 | Screen vertical/use cases before investment and launch |
| 2025 Cybersecurity Law changes | [Government policy summary](https://xaydungchinhsach.chinhphu.vn/nhung-noi-dung-moi-trong-luat-an-ninh-mang-so-116-2025-qh15-119260629145615168.htm) | 2026-07-01 | Security, incident, data/system obligations require counsel interpretation |
| E-invoice changes, Decree No. 70/2025/ND-CP | [Government legal database](https://chinhphu.vn/?classid=1&docid=213179&orggroupid=2&pageid=27160) | Per official instrument | Relevant to compliance-to-cash and invoice integrations |

This is issue spotting, not legal advice. The company needs a current applicability memorandum from qualified Vietnamese counsel before the affected launch.

## Frontier agent engineering evidence

### Anthropic

| Source | Adopted lesson | Limitation |
| --- | --- | --- |
| [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | Evaluate model+harness in production-like environments; use outcome, transcript review, mixed graders, repeated trials | Vendor engineering guidance; adapt to our risk/domain |
| [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | Treat context as finite attention; select and compress deliberately | General guidance, not a data-authority design |
| [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) | Initialize artifacts/task state, work incrementally, leave resumable progress | Primarily coding-agent pattern; use only for suitable internal tasks |
| [Harness design for long-running applications](https://www.anthropic.com/engineering/harness-design-long-running-apps) | Add generator/evaluator/planner scaffolds only with evidence and ablation | Vendor-specific experiments may not generalize |
| [Managed agents](https://www.anthropic.com/engineering/managed-agents) | Separate append-only session, harness, sandbox, and stable brain/hands boundary | Design input, not an instruction to adopt the service |
| [Multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) | Multi-agent systems help independent breadth tasks but cost substantially more tokens and struggle with dependence | Reported on Anthropic's workload; token multiple is not universal |
| [Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) | Prefer a few clear task-oriented tools and evaluate tool usability | Tool guidance does not replace authorization |

### NVIDIA

| Source | Adopted lesson | Limitation |
| --- | --- | --- |
| [AI-Q blueprint architecture](https://docs.nvidia.com/aiq-blueprint/2.0.0/architecture/overview.html) | Deterministically route direct, shallow, and deep work; put HITL before deep execution | Reference blueprint; infrastructure may exceed SME-stage needs |
| [Enterprise research-agent reference architecture](https://docs.nvidia.com/enterprise-reference-architectures/ai-q-research-agent-blueprint/latest/introduction.html) | Enterprise identity, deployment, and observability belong in the architecture | Research workflow, not transactional authority model |
| [NeMo Agent Toolkit](https://docs.nvidia.com/nemo/agent-toolkit/1.2/index.html) | Keep functions model/framework-neutral and support profiling/eval/observability | Product docs; no evidence we need the toolkit itself |
| [Evaluation workflow](https://docs.nvidia.com/nemo/agent-toolkit/latest/workflows/evaluate.html) | Record effective config, intermediate steps, latency, tokens, models, errors, and repeated trials | Implementation details evolve |
| [Observability](https://docs.nvidia.com/nemo/agent-toolkit/latest/run-workflows/observe/observe.html) | Event-driven exporters and OpenTelemetry-compatible visibility | Telemetry must still be privacy-controlled |
| [Security considerations](https://docs.nvidia.com/nemo/agent-toolkit/1.7/resources/security-considerations.html) | Prompts, responses, and logs can carry sensitive information | Baseline warning, not complete threat model |

### OpenAI, Google, and Microsoft

| Source | Adopted lesson | Limitation |
| --- | --- | --- |
| [OpenAI — Agents SDK vs Responses API](https://developers.openai.com/api/docs/guides/agents#agents-sdk-vs-responses-api) | Responses API fits an application-owned loop; a managed SDK adds orchestration/session/tracing/approval facilities | Repository contracts determine actual adapter; docs may change |
| [OpenAI — Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices#design-your-eval-process) | Define task-specific evals, inspect failures, and iterate systematically | Provider guidance, not sufficient release evidence |
| [OpenAI — API data controls](https://developers.openai.com/api/docs/guides/your-data#v1responses) | Verify endpoint-specific storage/retention behavior | Contract/account/region may change; DEC-006 remains controlling |
| [Google — Gemini Enterprise Agent Platform, 2026](https://cloud.google.com/blog/products/ai-machine-learning/the-new-gemini-enterprise-one-platform-for-agent-development) and [introduction](https://cloud.google.com/blog/products/ai-machine-learning/introducing-gemini-enterprise-agent-platform) | Enterprise agents need identity, runtime, memory profiles, simulation, live eval, observability, and governance | Company product publication; broad platform positioning |
| [Microsoft — Durable agents](https://learn.microsoft.com/en-us/azure/durable-task/sdks/durable-agents-microsoft-agent-framework) and [durable extension](https://learn.microsoft.com/en-us/agent-framework/integrations/durable-extension) | Persist sessions/checkpoints and support crash recovery and long-lived human approval | Technology option; adopt only on measured trigger |

## Vertical-agent and enterprise-product evidence

| Source | Adopted lesson | Limitation |
| --- | --- | --- |
| [Harvey agentic platform updates](https://www.harvey.ai/blog/agentic-platform-updates) and [Copilot/Cowork](https://www.harvey.ai/blog/harvey-copilot-cowork-launch) | Deep domain workflows, expert-built/tested agents, and integration into existing work surfaces can create value | Company-reported product claims in legal domain |
| [Harvey customer adoption example](https://www.harvey.ai/blog/maddocks-advances-its-ai-strategy-by-rolling-out-harvey-enterprise-wide) | Rollout across real practice teams and daily/weekly use are more meaningful than seats | Company/customer-reported single example; not general evidence |
| [Sierra — outcome-based pricing](https://sierra.ai/blog/outcome-based-pricing-for-ai-agents), [engineering](https://sierra.ai/blog/engineering), and [gradual rollout](https://sierra.ai/blog/better-customer-experiences-built-on-sierra) | Test measurable outcome units, context/long-horizon engineering, A/B and gradual rollout | Company-reported; local economics and safety differ |
| [Palantir AIP release notes](https://www.palantir.com/docs/foundry/announcements/release-notes?filters=aip) and [workflow example](https://aip.palantir.com/workflow/b1e5d1f7-8fe5-4e67-9588-3f409f1e0bea) | Permission-aware current enterprise state, actions, ontology, and feedback are central | Enterprise platform examples, not SME validation |
| [Glean](https://www.glean.com/) | Permission-aware enterprise context/connectors/governance are category expectations | Vendor website; claims require independent validation |

## Independent safety, protocol, and research evidence

| Source | Adopted lesson | Limitation |
| --- | --- | --- |
| [Tau-bench, ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/1b126cc38b8638e07bef37e7b2bb72bf-Abstract-Conference.html) | Tool agents can be inconsistent across multi-turn interactions; repeated-task evaluation matters | Benchmark domains differ from Vietnam operations |
| [NIST AI RMF Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) | Use lifecycle risk governance, measurement, transparency, and incident learning | Voluntary US framework; not Vietnam legal compliance |
| [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) | Use audience-bound authorization/token hygiene at protocol boundaries | Protocol security is not application authorization |
| [A2A protocol](https://a2a-protocol.org/latest/) | Agent interoperability can serve real trust/organizational boundaries | Avoid needless internal distributed-agent complexity |
| [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) and [AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html) | Threat-model excessive agency, tool abuse, memory/context poisoning, identity, supply chain, and exfiltration | Community guidance; map to concrete system threats/tests |

## Conclusions supported by combined evidence

The following are explicit synthesis/inference, not direct quotations from any one source:

- A model-independent harness, deterministic authority layer, and production-like eval system are more durable investments than a framework or model bet.
- Vietnam's market size and channel reach create opportunity, but sustained adoption and policy/data readiness are likely the binding constraints.
- Vertical workflow depth and integration into existing systems are a more credible wedge than a horizontal autonomous-agent claim.
- Multi-agent execution should be confined to independent high-value work until it proves better than one bounded agent.
- Dedicated early tenancy is a reasonable risk/evidence strategy, but its economics must be tested.
- Legal, provider, and channel posture must be continuously revalidated in 2026; a static compliance statement is unsafe.

## Research maintenance

- Revalidate unstable market, law, provider, channel, model, and product claims before any decision or external publication.
- Preserve access date, source version, extracted claim, and limitation in the research record.
- Prefer primary official sources and use at least one independent source for material vendor claims where available.
- Label company figures and case studies as self-reported.
- Move approved decisions to the decision register; never leave them implicit in a research note.
