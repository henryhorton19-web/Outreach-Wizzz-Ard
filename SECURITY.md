# Security

## Scope
Research and personal-use infrastructure. This is not an operated service and
there is no production deployment.

## Reporting
Email the address on the GitHub profile. Please do not open a public issue for a
suspected vulnerability.

## Out of scope
- Findings requiring credentials that are not published here. None are.
- Third-party service behaviour reachable through this software.

## Secrets policy
No credential of any kind is committed. Configuration ships as `.env.example`
with placeholder shapes only. Secret scanning runs over the full history with
the default ruleset extended, not replaced.
