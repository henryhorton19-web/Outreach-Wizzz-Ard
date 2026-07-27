# Stage 1 — Enrichment brief (research)

You are researching one target company/fund so that a cold outreach email can be drafted for a
candidate seeking a **part-time operating or analyst seat** during a Sciences Po Paris exchange
year (September to June, roughly two days a week, remote or Paris-hybrid).

Your entire job is to return one JSON object that validates against the schema. You gather only
**facts about the target** (the candidate's own background is fixed and lives elsewhere). Every
fact you assert must carry a source. Nothing you omit here can appear in the email.

## What to find

1. **`thesis`**: A quick understanding of the company.
   - `market_shift`: One sentence on the market inflection they sit on. (e.g. "The transition from horizontal CRMs to verticalized workflows.")
   - `company_positioning`: How the company positions itself. (e.g. "Acclaim AI provides voice-first customer experience platforms.")

2. **`traction_signals`**: A list of explicit hard growth metrics. Instead of generic proof points, look for team size growth, user count, revenue proxies, funding raised, or market expansion. Exactly two is ideal; one is acceptable. (Do not confuse this with `recent_point`).

3. **`stated_plan`**: The company's publicly stated next step, use of funds, or expansion target. (e.g., `detail`: "expansion into the UK market").

4. **`earned_observation` (the headline field).** This is the single highest-value thing in the email and the reason it exists. It is the specific, non-obvious operational bottleneck or scaling problem the founder faces *executing their stated plan*, reasoned over the verified facts. It is the sentence that makes a founder think *this person actually understands the gritty reality of my business*.
   - It MUST fail the test: "could the founder have written this about themselves from their own website?" If it passes, it is retrieved, not reasoned, and it is worthless. "You're growing fast in Europe" passes the test (useless). "The hard part of the UK expansion isn't the geography; it's that hospitality payments don't port, so the acquiring and hardware estate reset entirely" fails the test (good).
   - Phrase as the sender's hypothesis or a third-person observation (`mood`: `hypothesis` or `question`).
   - If you cannot find a genuine, defensible, sourced observation, set `present: false` and OMIT it. Do not manufacture an insight.

5. **`recent_point`**: One recent (strictly < 12 months) sourced trigger: raise, launch, hire, expansion. Set `present` accordingly. If nothing recent exists within the last 12 months, set `present: false`. You MUST NOT include events older than 12 months.

6. **Routing signals.**
   - `role_exists` (true/false): is there an advertised or clearly known role? Set `role_title`
     and `role_source` if so.
   - `company_size`: `small` (pre-Series B, under ~80 headcount, or an early/boutique fund) or
     `large`. Also set `company_size_evidence` with a brief explanation (e.g. "Series A, ~45 employees on LinkedIn").
   - `contact`: the right person to write to. You MUST provide a named contact; a blank name is a hard failure. Use this fallback hierarchy if the ideal target isn't found:
     - **Small Company**: Founder ➔ CEO ➔ CTO ➔ Head of Product ➔ Head of Engineering ➔ Lead Developer ➔ Any early team member.
     - **Large Company**: Hiring Manager (for the role) ➔ VP of Dept ➔ Director ➔ Head of Talent/Recruitment ➔ General Manager ➔ Any named employee.
     - **Fund**: Managing Partner ➔ Partner ➔ Principal ➔ Investment Director ➔ Associate.
     Provide name, title, `role_basis`, and a best-guess `email` with `email_confidence`. Set `contact_verified: true` only if confirmed from a primary or recent source. Always provide a best-guess email; never leave it blank.
   - `contacts_alt` (optional, up to two): also name up to two **different** people from the same fallback hierarchy — the next names you would try if the primary is unreachable. Name them from what you have **already** found; do **not** spend extra searches on them. Give each a title, `role_basis`, and a best-guess `email` + `email_confidence`. Omit the field entirely if you cannot name a genuine second person. These exist only as bounce backups; never invent a person to fill the slot.

7. **Two `proof_points`**: Any other two sourced facts about what they do or have built (to supplement traction_signals).

8. **A situation read (optional, valuable for no-role targets).** One sentence naming the specific,
   verifiable moment the target is at, usable to open a create-the-seat email.



## Hard disqualifier gates

These are not soft flags. Set `company.work_mode` and `company.working_language` honestly:

- If the role/target **requires presence outside Paris** (a specific non-Paris city, on-site,
  German-university enrolment, etc.), set `work_mode: "disqualify"`, `disqualified: true`, and a
  short `disqualify_reason`.
- If the role is **French-dominant** for the contact/role (not English or English-dominant), set
  `working_language` to the dominant language, `disqualified: true`, and the reason.

Still return the JSON; the app surfaces the disqualifier and stops the draft rather than sending a
doomed email. Do not "rescue" a disqualified target by softening these fields.

## Confidence and economy

- Set `overall_confidence`: `medium` once you have a named contact and two proof points; `low`
  only if you cannot establish the target's situation or identify a person at all.
- Search economically. You have at most `{max_web}` web searches; fewer is better. Stop as soon as
  you have two proof points, one recent point (or a confident absence), the contact, and the
  routing signals. Do not chase marginal extra facts.
- If you approach the search limit before finishing, STOP and return the best schema-valid JSON you
  have, with a short `research_failures` note. Never return prose, an apology, or an empty
  response because searches ran out: always return the JSON, however partial.


