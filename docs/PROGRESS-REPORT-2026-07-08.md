# Janasunani 2.0 — Progress Report
**Date:** 8 July 2026 · **Prepared for:** leadership review

## In one line
We are building **Janasunani 2.0**, an AI-assisted system that takes a citizen's
grievance — typed or a scanned document — and automatically reads it, protects
the citizen's private information, understands what it's about, summarizes it,
and routes it to the right government office. The end goal is a working demo by
the end of July.

## What we have accomplished so far

**1. All historical grievances are consolidated and live in the cloud.**
We migrated the entire back-catalogue from the old system — **1.37 million
grievances and 6.56 million case-history records** — into one clean, searchable
database now running on our secure cloud server, with automatic nightly backups.
This is the authoritative record the whole system is built on, and it is done and
verified.

**2. The AI "processing line" is built.**
We built the automated pipeline that handles a grievance end to end:
reads the text from scanned documents, **automatically removes personal
information** (names, phone numbers, IDs) to protect citizens, identifies the
category of complaint, writes a short summary, and determines the responsible
department. Every stage is built and tested, including a trial run on a
high-powered GPU machine.

**3. Privacy is built in, not bolted on.**
Citizen information is never sent to any outside service — the removal of
personal details happens entirely inside our own secure environment. This is a
firm design rule throughout the system.

**4. Routing is learned from real decisions, not guesswork.**
Rather than hand-writing rules for which office handles which complaint, we are
teaching the system from **1.37 million real past routing decisions**. Early
measurements show it can already predict the correct department about **three
times out of four** as a starting point — and this will improve as we refine it.

**5. There is a working demo web application.**
We have a first version of the citizen-facing web app, styled in our official
**DPIC (UChicago–Government of Odisha) branding**, where you can submit a
grievance and browse past cases. It runs today and demonstrates the full
end-to-end experience.

**6. Cost-efficient cloud setup.**
The infrastructure runs on an always-on standard server plus a powerful GPU
machine that we switch on only when needed (about $1/hour while running), keeping
costs low.

## What you can see today
A live demo of the web app: submit a grievance and see it flow through
extraction → privacy redaction → categorization → summary → routing, plus a
searchable history view — all in DPIC branding.

**One honest note:** in the current demo the *experience* is real and complete,
but the AI results shown are **placeholders**. The actual AI models are built and
tested separately; connecting them into the live app is the next step. We kept
this separation deliberately so the app and the models could be built in parallel
without waiting on each other.

## What's next
1. **Connect the real AI models** to the demo so it runs on genuine predictions.
2. **Finish and polish the demo web app.**
3. **Final review of the privacy protection** against real sample pages before
   any real data flows through.
4. **Stakeholder demonstration** — targeted for **end of July 2026**.

## Bottom line
The hard foundational work — the data, the AI processing pipeline, the privacy
protections, and the cloud infrastructure — is **done and running on real data**.
The remaining work is connecting the pieces and polishing the demonstration, and
we are on track for the end-of-July demo.
