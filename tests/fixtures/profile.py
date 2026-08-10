"""Synthetic seeker profile for tests. No test may read the user's real profile.

Four experiences, chosen to exercise every branch of rank_evidence:
  anchor_co  standing_key target; tense=present (exercises the ongoing-role guard)
  fund_co    fallback_key target; domain-shaped bridges
  tech_co    domain-shaped bridges, no overlap with fund_co
  side_co    optional=True, gated on ownership/ops signals; function-shaped bridges
"""

FIXTURE_PROFILE = {
    "name": "Test Seeker",
    "email": "seeker@example.com",
    "phone": "+00 0000000000",
    "linkedin": "Test Seeker",
    "education": {"primary": "Test University, BSc, 2024 to 2028."},
    "one_line": "A test seeker after a test seat.",
    "spine": "I build things and I measure them.",
    "standing_key": "anchor_co",
    "fallback_key": "fund_co",
    "experiences": {
        "anchor_co": {
            "name": "Anchor Co", "title": "Analyst", "when": "Jun 2026 - present",
            "tense": "present",
            "anchor": "I am an analyst at Anchor Co working on growth software.",
            "facts": ["Built a cohort model covering 40 logos."],
            "bridges": ["analytical", "builds", "fintech_ai"],
            "domains": ["private_markets", "saas_metrics", "growth_equity"],
        },
        "fund_co": {
            "name": "Fund Co", "title": "Intern", "when": "Jan 2026 - Apr 2026",
            "tense": "past",
            "anchor": "I worked on a fundraise at Fund Co.",
            "facts": ["Supported a 160m raise."],
            "bridges": ["fundraising", "investor_adjacent", "analytical", "builds"],
            "domains": ["private_markets", "sourcing_automation", "fundraising"],
        },
        "tech_co": {
            "name": "Tech Co", "title": "Engineer", "when": "Feb 2026 - Mar 2026",
            "tense": "past",
            "anchor": "I built a credit model at Tech Co.",
            "facts": ["Shipped a scoring pipeline."],
            "bridges": ["ai_native", "fintech_ai", "technical", "builds", "research"],
            "domains": ["fintech_ai", "automation", "saas_metrics"],
        },
        "side_co": {
            "name": "Side Co", "title": "Co-founder", "when": "2023 - 2025",
            "tense": "past", "optional": True,
            "anchor": "I co-founded Side Co and ran operations.",
            "facts": ["Grew it to 100 users."],
            "bridges": ["ownership", "zero_to_one", "ops", "builds"],
            "domains": ["ops", "zero_to_one"],
        },
    },
    "signals": ["A credibility signal."],
}
