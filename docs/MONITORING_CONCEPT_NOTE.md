# Monitoring grievance redressal

**Concept note outline for the CM Grievance Cell and GA&PG**

# Executive summary

- Build a monitoring layer that shows where grievances stall, whether required reviews take place and whether closure settles the citizen's problem.
- The first build should cover:
  - flow, aging and routing bottlenecks;
  - ATR receipt, review and queue discipline;
  - closure quality, reopening and repeat submission;
  - detection of pure duplicates, follow-ups, related grievances and campaigns;
  - state and department views.
- Duplicate and follow-up detection should produce evidence-based candidate labels, confidence and an uncertain result. Government should decide what action each confirmed label triggers.
- Every view should show the source period, numerator, denominator, coverage and whether a measure is direct or a proxy. Raw filings, identity-linked keys and deduplicated problem groups should remain separate.
- Monitoring depends on the application recording intake, routing, ATR, review, closure and case-relationship events consistently.
- Use the CM Grievance Cell application rollout to add the required fields and test the views. The rollout is an implementation opportunity, not the main purpose of the work.
- Before implementation, government should confirm review rules, relationship definitions, operational actions, audit permissions, dashboard users and accountable owners.

# 1. Monitor ATR and review

## 1.1 ATR receipt and quality

- What share of grievances receives an ATR?
- At what stage is the ATR requested and received?
- How long does an ATR wait before review or closure?
- What does the ATR contain?
  - action taken;
  - no action taken, with reason;
  - request for more time or information;
  - documents or other evidence.
- Does the ATR address the original grievance?
- Do ATR remarks agree with the later review decision and closure record?
- Does an ATR improve the citizen's outcome, or only complete an administrative step?
- Do not treat receipt of an ATR as proof of resolution.

## 1.2 Required and completed review

- Establish the **de jure review rate**:
  - which grievances are required to be reviewed;
  - when review is compulsory;
  - which officer or office is responsible;
  - the time allowed for review.
- Measure the **actual review rate**:
  - grievances reviewed;
  - grievances closed without a recorded review;
  - grievances still waiting for review;
  - reviews completed after the grievance was otherwise treated as closed.
- Check the sequence of events:
  - ATR received;
  - review started;
  - ATR accepted, returned or reassigned;
  - grievance closed or reopened.
- Check whether review decisions record:
  - date;
  - reviewer role;
  - decision;
  - reason;
  - link to the ATR reviewed.

## 1.3 Review action

- Accept the ATR.
- Return it for clarification.
- Ask for further action.
- Ask for a factual enquiry.
- Reopen or reassign the grievance.
- Close the grievance with a reason.
- Record which of these actions was taken rather than treating all review as one event.

## 1.4 What happens after review

- Compare reviewed and non-reviewed grievances with similar case characteristics.
- Examine:
  - time to closure;
  - quality and specificity of the closing remark;
  - reopening after closure;
  - repeat submission of the same problem;
  - citizen-reported outcome, where available.
- Conduct a random audit of ATRs and closures.
- Contact a sample of citizens after closure.
- Use these checks to distinguish administrative disposal from actual resolution.

## 1.5 Monitoring gaps and responses

- **Generic closure remarks**
  - Issue: the record may say that a grievance was disposed without explaining what was done.
  - Response: require a fixed closure reason, a case-specific remark and supporting evidence where relevant.
  - Check: audit a random sample against the underlying action.
- **Unclear review coverage**
  - Issue: the rule for which grievances require review is not yet clear in the analytical record.
  - Response: define compulsory review and record every review as a dated event.
  - Check: compare the share due for review with the share actually reviewed.
- **Review without evidence of impact**
  - Issue: a review action does not show whether the grievance was resolved better.
  - Response: link the ATR, review decision, closure, later returns and citizen feedback.
  - Check: compare outcomes for similar reviewed and non-reviewed grievances.
- **Weak queue discipline**
  - Issue: older ATRs may remain pending while newer ATRs are disposed.
  - Response: show aging, apply escalation rules and record reasons for exceptions.
  - Check: measure the number and duration of FIFO exceptions.
- **Recurring problems without an owner**
  - Issue: the same intake, routing or closure problem can recur without corrective action.
  - Response: maintain an issue register with the responsible office, action and due date.
  - Check: monitor whether the issue recurs after the action is completed.

# 2. Monitor flow and intake

## 2.1 Show the full grievance flow

- Map the recorded path:
  - entry;
  - classification;
  - first assignment;
  - transfers and routing;
  - field action;
  - ATR request and receipt;
  - review;
  - closure, return or reopening.
- Show movement:
  - within an office;
  - across offices;
  - back to an earlier office.
- Measure:
  - time at each stage;
  - cases with no recent activity;
  - repeated transfers;
  - routing loops;
  - queues by age;
  - stages where cases accumulate.

## 2.2 Understand discards

- Establish whether each discard occurs:
  - before routing;
  - after routing;
  - after an officer has started action;
  - after an earlier ticket has been located.
- Separate:
  - spam or non-grievances;
  - inadequate details;
  - missing documents;
  - matters outside the Cell's purview;
  - matters requiring a policy decision;
  - duplicate or already-open matters.
- Report discard reasons and timing together.
- Do not interpret all discards as the same operational failure.

## 2.3 Detect duplicates and follow ups separately

- Produce candidate labels, not workflow decisions:
  - pure duplicate;
  - follow-up;
  - related but distinct grievance;
  - campaign or multi-signatory filing;
  - uncertain.
- Government should decide what operational action follows each label.
- **Find possible matches**
  - For pure duplicates, reuse the existing duplicate search to find text-similar pairs and pairs sharing an identity-linked key.
  - Treat a shared text band or identity-linked key as a candidate, not as proof of duplication.
  - Verify near-identical text candidates using exact similarity on redacted grievance text.
  - For follow-ups, first use any explicit earlier-ticket reference.
  - Where no reference is supplied, search earlier filings with the same identity-linked key and matching problem attributes or sufficient text similarity.
  - Do not require near-identical wording for a follow-up candidate.
- **Compare each candidate pair**
  - identity-linked key match, where available;
  - exact and near-duplicate text similarity;
  - complaint category, department, place, scheme or service;
  - time between filings and the earlier ticket's status;
  - reference to an earlier filing, request for status, or statement that the problem continues;
  - new facts, dates, documents or requested action in the later filing.
- **Assign a candidate label**
  - Pure duplicate candidate: same identity-linked key, identical or near-identical problem description, and no detected material new information.
  - Follow-up candidate: an identity-linked or explicitly referenced earlier ticket concerning the same problem, plus a status request, evidence that the problem continues, or material new information.
  - Related grievance candidate: similar subject, but a different event, period, entitlement or requested action.
  - Campaign candidate: highly similar text submitted through different identity-linked keys.
  - Uncertain: available evidence does not separate these cases reliably.
- **Validate before use**
  - Build a manually reviewed sample covering languages, departments, time gaps and open and closed earlier tickets.
  - Have a second reviewer check disagreements between pure duplicates and follow-ups.
  - Measure precision and recall separately for each label.
  - Give special attention to follow-ups wrongly labelled as pure duplicates.
  - Set confidence thresholds from the reviewed sample and retain an uncertain category.
- **Monitor detection quality**
  - show candidate and confirmed counts separately;
  - report identity and text coverage;
  - report confidence and error rates by label;
  - keep raw filings separate from analytically grouped problem counts.
- Do not treat privacy-protected identity keys as verified people.

## 2.4 Understand routing

- Show the route actually taken by each grievance.
- Identify:
  - avoidable transfers;
  - returns to an earlier office;
  - long waits after transfer;
  - routes associated with faster substantive action.
- Test category and destination suggestions with officers.
- Record whether the officer accepts or changes a suggestion and why.
- Measure officer time saved directly during testing.
- State the limits of that estimate; do not infer time saved from historical routing alone.

# 3. How the analytics should be viewed

## 3.1 About the data

- Show:
  - source period and last refresh;
  - scope and filters;
  - numerator and denominator;
  - missing fields and coverage;
  - whether the measure is direct or a proxy.
- Keep complaint counts separate from:
  - identity-linked keys;
  - deduplicated problem groups;
  - verified citizens, which the current record does not establish.

## 3.2 State view

- For the CM Grievance Cell and GA&PG.
- Show:
  - bottlenecks across the system;
  - intake quality and discard patterns;
  - complaints outside the Cell's purview;
  - transfer and routing patterns;
  - ATR and review backlogs;
  - required versus actual review;
  - closure quality and return after closure;
  - departmental and geographic patterns.
- Use the view to identify system problems and assign follow-up.
- Avoid simple departmental rankings unless workload and case mix are accounted for.

## 3.3 Department view

- For a Secretary and the department's own administrative chain.
- Show:
  - variation across districts, blocks and offices;
  - where cases stall within the department;
  - ATR and review queues by office;
  - chronic problems versus sudden surges;
  - scheme-linked grievance patterns, where a mapping exists;
  - cases routed to the department in error.
- Use the view to locate the office and stage where action is needed.

# 4. Tools to build and test

- **Flow dashboard**
  - shows the full pipeline and main bottlenecks;
  - supports state and department views.
- **Intake assistant**
  - checks completeness;
  - flags possible spam, discards, pure duplicates, follow-ups and related grievances;
  - shows the reason for each flag.
- **Duplicate and follow-up detector**
  - retrieves possible earlier tickets;
  - proposes a candidate relationship with a confidence score;
  - shows the evidence used and allows an uncertain result;
  - does not choose the operational action.
- **Classification and routing assistant**
  - proposes category and destination;
  - records acceptance, correction and reason.
- **ATR and review workspace**
  - orders the queue by age;
  - shows the grievance, supporting documents and earlier actions;
  - records the review decision and reason.
- **Closure check**
  - flags generic remarks;
  - requires a closure reason and case-specific account of action taken.

# 5. What the current record establishes

- Available analytical record:
  - 1,371,288 complaints;
  - 6,556,171 action entries.
- Closure records are often too generic to show what happened:
  - 776,922 complaints were closed using one of six standard templates;
  - 472,782 used the template recording no action;
  - this is 60.9% of templated closures and 39.1% of all 1,209,144 resolved complaints;
  - 8,974 complaints were created and closed within two days using that template.
- Officers have marked 37,299 action entries as duplicates.
- Common recorded discard reasons include:
  - inadequate complaint details: 39,964;
  - required documents missing: 29,029;
  - case already taken up: 21,117;
  - no specific grievance: 16,375;
  - duplicate copy: 16,182;
  - policy decision required: 9,125;
  - outside the grievance cell's purview: 8,472;
  - address missing: 4,114.
- Existing routing test results provide a baseline:
  - historical destination matched at first choice: 45.1%;
  - historical destination included in the top three: 69.0%;
  - later-period test set: 208,267 complaints.
- Routing agreement does not establish that the historical destination was correct.

# 6. What the application should record

- Entry channel and date.
- Classification and every later change.
- Assignment, transfer and return as separate dated events.
- Discard reason and whether discard occurred before or after routing.
- Explicit reference to an earlier ticket, where supplied.
- Candidate relationship to an earlier ticket: pure duplicate, follow-up, related grievance, campaign or uncertain.
- Detection evidence: identity linkage available, text similarity, matching problem attributes, time gap, earlier status and new information supplied.
- Confidence score and model or rule version.
- Reviewed relationship label and reason.
- Operational action selected under the government's policy, recorded separately from the analytical label.
- ATR request, receipt, return, acceptance and closure as separate dated events.
- Whether review is required.
- Review date, reviewer role, decision and reason.
- Fixed closure reason and case-specific closing remark.
- Supporting evidence where required.
- Reason for changing a proposed category or route.
- Citizen contact before closure.
- Citizen feedback after closure.
- Scheme or service, where applicable.

# 7. Testing and implementation

- Start with a documented baseline for current processing.
- Test selected tools alongside the current process.
- Keep analytical labels separate from government workflow decisions.
- During testing, the duplicate and follow-up detector should produce labels only.
- Record:
  - the tool's suggestion;
  - the reviewer's confirmed relationship label;
  - any correction or override;
  - the suggested and confirmed relationship to an earlier ticket;
  - time taken;
  - the later review and outcome.
- Use the CM Grievance Cell application rollout as the first opportunity to add the required fields and test the views.
- Extend to a line department once the state view and review measures are stable.

# 8. Decisions needed

- Define which grievances require review and what a completed review means.
- Agree how spam and incomplete submissions will be treated.
- Confirm the analytical definitions of pure duplicates, follow-ups, related grievances and campaign filings.
- Decide what operational action, if any, each confirmed label should trigger.
- Decide whether and when a possible earlier ticket should be shown to the citizen.
- Confirm the ATR and review events to be recorded in the application.
- Permit a random audit of ATRs and closures.
- Permit outcome follow-up with a sample of citizens.
- Confirm the state and department dashboard users.
- Name owners for:
  - data quality;
  - workflow and product changes;
  - review rules and escalation decisions;
  - issue follow-up;
  - evaluation and reporting.
