# Contributing

## Setup
See the Quickstart in `README.md`. It is written to work from a clean clone; if
it does not work for you, that is a bug in the README worth reporting.

## Before opening a PR
- The full test suite passes locally.
- Lint and type gates pass. See `.github/workflows/` for the exact set.
- No real personal data, company data, or credentials in code, tests, fixtures
  or documentation. Use `example.com` or a `.test` / `.example` / `.invalid`
  domain (RFC 2606) and clearly fictional names.
- One logical change per commit, Conventional Commits format.

## Not accepted
- Formatting-only changes to logic files.
- Dependency version bumps without a stated reason.
