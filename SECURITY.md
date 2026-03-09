# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | Yes                |

## Reporting

If you discover a security issue, please report it via
[GitHub Security Advisories](https://github.com/agent-kernel/agent-kernel/security/advisories/new)
rather than opening a public issue.

## Response Timeline

- **Acknowledgment:** Within 48 hours of report
- **Assessment:** Within 7 days
- **Fix:** Within 90 days for confirmed issues

## Scope

The following are in scope:

- Credential or secret exposure through library behavior
- Input injection (command, SQL, template, etc.)
- Unsafe deserialization or arbitrary code execution
- Authentication or authorization bypass in the API server

Out of scope: issues in dependencies (report upstream), social engineering,
and denial-of-service via expected resource usage.

## Disclosure

We follow coordinated disclosure. We will credit reporters in the changelog
unless anonymity is requested.
