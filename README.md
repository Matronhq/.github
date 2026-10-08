# Matronhq/.github

Shared GitHub configuration for Matron repositories.

## leak-check

`.github/workflows/leak-check.yml` is a reusable workflow that fails a pull
request or push when it adds private context: names, internal hosts,
references to private systems. The patterns live in the organisation secret
`LEAK_PATTERNS` (one Python regex per line), so the list is never published,
and a hit is reported by rule number and location only.

Use it from a repository with `caller-example.yml` (copy it to
`.github/workflows/leak-check.yml`). Paths that may legitimately match can be
listed, one glob per line, in `.leakcheck-allow`.
