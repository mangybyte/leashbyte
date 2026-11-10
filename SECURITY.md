# Security policy

LeashByte is pre-alpha. Please don't use it to process sensitive data yet.

## Reporting a vulnerability

Email `security@leashbyte.com`, and please don't open a public issue for a suspected vulnerability.

Tell us the affected version, how to reproduce the problem and what you think the impact is. Logs help, but take credentials and personal data out of them first.

That address is the right place for suspected credential leaks, access control faults, SSRF, prompt injection with a concrete effect and unsafe browser behavior. We'll acknowledge your report within five working days, and then work with you on a fix and on coordinated disclosure.

## Security model in brief

Out of the box, the API and the MCP server bind to loopback, and fetch and browser traffic can't reach loopback, private, link-local or cloud-metadata addresses. A private destination is only reachable after an administrator adds it explicitly.

The model never receives vault secrets or provider keys. Browser access and CDP access need separate authorization. Plugins are off by default and only run once an administrator enables them.

The full threat model is in [docs/DESIGN.md](docs/DESIGN.md).

## How we scan

CI runs lint, the test suite and the Python and JavaScript dependency licence checks on every push to `main` and on every pull request. GitHub secret scanning is turned on for this repository, with push protection. Changes to server-side fetch, browser access or the vault also get a dedicated security review before they're merged. Passing all of that doesn't prove a change is safe, though, so reports are always welcome.
