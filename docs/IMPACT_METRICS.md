# Impact metric registry

Model accuracy, officer behavior, workflow performance and citizen outcomes are
different claims. This registry prevents an offline benchmark or technical
latency from being reported as time saved or public impact.

## Impact ladder

| Level | Metric | Definition | Status |
|---|---|---|---|
| Model | Actionability harmful-review rate | Actionable grievances sent to review / adjudicated actionable grievances | Frontier-adjudicated developmental estimate available; officer-confirmed five-class gold required for release |
| Model | Routing top-1/top-3 agreement | Historical destination appears in ranked suggestions / eligible held-out grievances | Developmental held-out available; not correctness |
| Model | Summary factuality/usefulness | Critical-fact recall, unsupported fact, contradiction, PII leak, usefulness 0–3 | Needs paired adjudication |
| Officer behavior | Accept/edit/reject/ignore | Stage decision events / model outputs shown | Needs append-only exposure + later decision events |
| Workflow | First meaningful action | Days from filing to first validated substantive action; p50/p90 and 7-day attainment with censoring | Computable after action taxonomy validation |
| Workflow | Transfer-free first assignment | Tickets with no validated authority transition after first assignment / exposed tickets | Needs transition-event semantics |
| Workflow | Officer burden | Active handling seconds, edits/clicks, substantive touches and packets / staffed officer-hour | Needs UI active-time and staffing denominator |
| Citizen | Resolution by 30/90 days | Resolution by horizon among filings mature enough to observe that horizon; unresolved mature filings stay in the denominator | Derivable with validated event dates and a frozen extract cutoff |
| Citizen | 90-day restricted mean time | Area under unresolved survival curve to 90 days | Derivable; preferred to resolved-only mean |
| Citizen | Repeat filing/reopen | Same-citizen same-problem filing or timestamped reopen within 30/90 days | Needs privacy-safe identity/problem linkage and reopen event timestamp |
| Citizen | Officer-recorded benefit | `benefitted` distribution with missingness over all eligible closures | Derivable with caveat; never label it citizen satisfaction |
| Citizen | Citizen satisfaction | Citizen-reported resolved/satisfied among respondents, plus invitations, responses, response rate and item missingness | Needs an approved portal, SMS, WhatsApp or call workflow and privacy-safe linkage |

## Required slices and safeguards

Every numerator is published with its denominator, support, confidence interval,
missingness/censoring and case-mix definition. Slice by language, channel/source,
district, gender and office-volume stratum when support permits; suppress small
cells. Do not label parity as fairness without addressing missingness and case
mix.

Satisfaction is reported with invitation and response rates by arm and major
slice. Respondent satisfaction alone is not a population satisfaction estimate;
nonresponse sensitivity is required before a citizen-level effect claim. For a
causal pilot, invite every eligible case at a pre-specified fixed horizon,
regardless of whether the administration has closed it. A post-closure-only
survey is useful descriptively but conditions on an outcome the intervention
may itself change.

Telemetry contains model/release IDs, confidence, output class, hashes,
shown/hidden/fallback and edit/decision flags. Narrative text belongs in a
separately governed store, never the general experiment event table.

## Pilot contract

Use a locked stepped-wedge rollout assigned to immutable intake-office
transfer-network clusters, stratified by district, volume and baseline transfer
rate. Analyze intention-to-treat; distinguish assigned from actually exposed;
generate hidden shadow predictions for controls. Lock the unit map, extract
hash, estimands, minimum detectable effect and pause rules before arm outcomes
are read.

Co-primary operational outcomes are seven-day first-meaningful-action attainment
and transfer-free first assignment. The primary citizen endpoint is 90-day
restricted mean time/resolution. Guardrails include harmful actionable review,
PII leakage, availability, p90 latency and 30/90-day resolution,
transfer/reopen/recontact harm. Offline model scores alone cannot support a
causal value-add claim.
