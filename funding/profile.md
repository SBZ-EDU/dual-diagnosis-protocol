# Startup Profile — master data for all applications

> این فایل «منبع حقیقت» همه‌ی درخواست‌هاست. هر چیزی را اینجا به‌روز کنید و
> `python funding/generate.py` را دوباره اجرا کنید تا همه‌ی پیش‌نویس‌ها تازه شوند.
> بخش‌های داخل [کروشه] را با اطلاعات واقعی پر کنید. اطلاعات تماس در `.env` است (گیت‌نادیده).

## Company

- **Name:** Hamrah — Dual Diagnosis Clinical Hub (همراه | مرکز هوشمند تشخیص دوگانه)
- **Stage:** Pre-seed, MVP live (launched August 2026), pre-revenue
- **Legal entity:** [TO REGISTER — Turkey Ltd or Estonia OÜ planned; unlocks US cloud programs]
- **Founder:** [FULL NAME] — technical founder, built the entire stack solo
- **Team size:** 1 (actively looking for a commercial co-founder — see Antler/İTÜ notes)
- **Contact:** configured in `.env` → `FUNDING_CONTACT_EMAIL`, `FUNDING_WHATSAPP`

## Problem

Dual diagnosis (psychosis/schizophrenia + substance use disorders, with BPD ± ADHD) is the
rule, not the exception — yet care is split between psychiatric and addiction services that
rarely talk to each other. In Iran and the Persian-speaking region (~110M speakers):

- There is **no Persian-language, evidence-based digital companion** for dual diagnosis.
- Official domestic protocols focus on addiction alone; dual-diagnosis coverage is minimal,
  so families improvise care with no guidance.
- Treatment costs span an extreme range; families cannot match clinical needs to what they
  can afford.
- Patients and caregivers have nobody to ask the small daily questions between appointments.

## Solution

A protocol-driven, AI-assisted companion platform — in Persian, where the patients are
(on Telegram — no app install, no VPN):

1. **Clinical protocol hub (web)** — a 16-section, evidence-based treatment protocol adapted
   from NICE CG120/NG58, APA 2020 and WFSBP for Iranian reality: budget-tiered treatment
   levels, local emergency pathways (123/115/1490/1480), telemedicine routes for remote regions.
2. **Telegram robot (@AI_Aiddiction_Assistant_bot)** — a 7-indicator clinical risk-monitoring
   engine with fully transparent scoring (0–76, action levels — same results as the reference
   implementation), role-aware AI Q&A (patient / family / clinician get differently-framed
   answers), longitudinal risk tracking with trend reports, a 14-part daily evidence-based
   education series, and an offline protocol-search fallback.
3. **Public news channel (@AI_Addiction_assistant)** — daily evidence-based education posts.
4. **AI backend (Cloudflare)** — RAG over 200+ papers and guidelines, LLM answers with
   citations, D1 response cache, usage telemetry, experiment tracking (W&B).

Safety-first by design: the assistant never replaces a physician, never gives doses to
patients/families, and every output carries a medical disclaimer.

## Traction

- MVP **live and verified end-to-end** (site, bot, channel, AI API — all deployed).
- Open-source codebase (GitHub: SBZ-EDU/dual-diagnosis-protocol + dual-diagnosis-rag).
- [NUMBER] early users onboarded since launch / [METRICS — fill in as they grow]
- Zero marketing spend so far — distribution plan is clinician + caregiver communities.

## Why now

- Substance-use + psychosis comorbidity is rising across the region; families are the
  de-facto care system and are reachable on Telegram today.
- LLM inference costs collapsed (our whole AI layer runs on a free-tier-friendly worker).
- Cloud-credit programs let a health-AI MVP scale inference before raising equity.

## Ask

- **Near term:** cloud/AI credits (inference + re-embedding the corpus, multilingual Persian
  embeddings) and mentorship on health-AI safety evaluation and go-to-market.
- **Equity programs:** pre-seed support to incorporate, add a commercial co-founder, and run
  a clinician pilot (10+ clinics) in Iran/Turkey.
- **Not asking for:** a priced round yet — traction first.

## Roadmap (12 months)

1. Clinician pilot + outcome logging (anonymized, consent-based)
2. Clinician dashboard (risk-trend overviews for caseloads)
3. Persian voice input (ASR) for low-literacy users
4. Arabic localization for MENA expansion
5. Publish a transparent model card + safety evaluation of the Q&A layer
