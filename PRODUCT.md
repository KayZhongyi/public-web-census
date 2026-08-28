# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary users are company leaders, market teams, presales teams, product teams, and other non-technical colleagues who already use Codex or Claude and need public-market evidence without operating a custom scraper.

## Product Purpose

Public Web Census—branded in Chinese as 察言观数—turns public social-web pages into refreshable, source-linked evidence that an AI Agent can analyze without treating generated prose as fact. Success means a colleague can install the Skill, describe an account and a business question in natural language, complete any required browser verification themselves, and receive reusable raw records plus an evidence-aware analysis.

## Positioning

Read public voices. Track visible signals. Ground AI in evidence. The product combines logged-in browser collection, stable record identities, incremental history, source links, and deterministic validation so that AI-assisted market, customer-voice, and presales work is grounded in inspectable public data.

## Operating Context

- Installed locally as an Agent Skill and used from Codex or Claude.
- Runs on macOS and Windows with local Python, Chrome, and OpenCLI where browser collection is required.
- The colleague supplies a target account or page and a business question in natural language.
- Human users retain control of platform sign-in, account confirmation, verification challenges, and final business judgment.
- Raw public records remain locally available as CSV and SQLite evidence history for later refreshes and new questions.

## Capabilities and Constraints

- TikTok: profile videos, visible engagement metrics, and selected visible comment or reply collection.
- Facebook: public Page posts and visible engagement metrics; comment collection is not currently implemented.
- YouTube: channel video metadata and selected visible comment or reply collection.
- LinkedIn: public or signed-in company and personal profile posts plus visible engagement metrics when available; comment collection is not currently implemented.
- Browser connectors use a user-authorized Chrome session, pause at platform verification, and do not bypass access controls.
- The product collects public page evidence and reduces hallucination risk; it does not guarantee that an AI model will never hallucinate.
- No centralized server, shared credential store, or scheduled enterprise deployment is currently included.

## Brand Commitments

- English product name: Public Web Census.
- Chinese product name: 察言观数.
- Repository and CLI name: `public-web-census`.
- Chinese and English documentation are first-class.
- Voice: concise, evidence-led, useful to business decision-makers, and explicit about real platform boundaries.
- Chinese promise: 察公开之言，观市场之数，让 AI 有据可依。
- Core message: reduce AI hallucinations by making public evidence traceable, refreshable, and reusable.

## Evidence on Hand

- Existing live collectors and automated tests in `scripts/` and `tests/`.
- Existing real YouTube terminal demonstration in `assets/youtube-live-demo.gif`, used only as connector proof rather than as a claim that all platforms behave identically.
- No approved customer testimonial, adoption benchmark, or public commercial outcome. The website must not fabricate these.
- The public product tour may demonstrate capabilities and process, but must not present fictional business records as real evidence.

## Product Principles

1. Evidence before interpretation.
2. Natural-language operation for business users; technical controls remain available for verification.
3. Preserve source, time, visible metrics, and stable identity so every conclusion can be checked and refreshed.
4. Human control at authentication, verification, and final judgment.
5. Describe current platform support exactly; do not market planned capability as shipped capability.

## Accessibility & Inclusion

The public product tour and documentation must work with keyboard navigation, visible focus, reduced motion, readable contrast, and responsive layouts on desktop and mobile.
