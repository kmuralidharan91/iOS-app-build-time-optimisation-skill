# Citation index — `references/sources.md`

> Every URL referenced from a Phase A finding citation, an analyzer rule
> docstring, or a SKILL.md "References" block, with the verification
> note required by project [`CLAUDE.md`](../../Desktop/Command+B/CLAUDE.md) Rule 1.

## Apple — Xcode build system + build settings

| ID | URL | Verified | Used by |
| --- | --- | --- | --- |
| `apple/build-settings-reference` | <https://developer.apple.com/documentation/xcode/build-settings-reference> | 2026-05-04 via WebFetch (page title confirmed; content lazy-loaded JSON behind the marketing shell — exact setting tables fetched at finding-evaluation time when needed) | F4, F9 build-setting findings |
| `apple/build-system` | <https://developer.apple.com/documentation/xcode/build-system> | 2026-05-04 via Tuist/Bazel quickstarts (resolved 200 OK; content stable across Xcode 14-26 cycle) | SKILL.md "References" |
| `apple/asset-management` | <https://developer.apple.com/documentation/xcode/asset-management> | 2026-05-04 (Apple-canonical landing page for `actool` / asset catalog reference) | F5 asset-catalog finding |
| `apple/swift-packages` | <https://developer.apple.com/documentation/xcode/swift-packages> | 2026-05-04 (Apple's Swift Package documentation hub) | F7 spm/oversized-module, R1 branch-pinned suppression |
| `apple/xcode-14-release-notes` | <https://developer.apple.com/documentation/xcode-release-notes/xcode-14-release-notes> | 2026-05-04 via WebFetch (resolves 200 OK) | F3 reference (script-phase output declarations) |
| `apple/xcode-26-release-notes` | <https://developer.apple.com/documentation/xcode-release-notes/xcode-26-release-notes> | 2026-05-04 via `curl -I` (HTTP/1.1 200 OK) — **prebuilt swift-syntax claim NOT verified line-by-line in this chat**; Phase A simulate must confirm exact setting + applicability before publishing the F6 fix recommendation | F6 spm/swift-syntax-not-prebuilt |
| `apple/xcodebuild-man` | <https://keith.github.io/xcode-man-pages/xcodebuild.1.html> | 2026-05-04 (mirror of `xcodebuild(1)` man page; Apple does not host an HTML man page) | adapter `show_build_settings`, SKILL.md |

## Apple — WWDC sessions

| ID | URL | Verified | Used by |
| --- | --- | --- | --- |
| `wwdc/2022/110364` | <https://developer.apple.com/videos/play/wwdc2022/110364/> | 2026-05-04 via WebFetch (session title "Demystify parallelization in Xcode builds" confirmed); transcript also on disk at `~/Desktop/Command+B/transcripts/xcode-build-parallelization-wwdc2022.md`, every quote in `build-settings-best-practices.md` `grep -F`'d against it | F1, F2, F3, F8 + PR-#2 sandboxing/fuse |

## Tuist + Bazel (carried forward from Phase A)

| ID | URL | Verified | Used by |
| --- | --- | --- | --- |
| `tuist/manifests` | <https://tuist.dev/en/docs/guides/features/projects/manifests> | 2026-05-04 — verified live via the Phase A redirect handling (`docs.tuist.dev` -> `tuist.dev/en/docs/...`); Tuist adapter `detect()` rule | adapter detection |
| `bazel/apple` | <https://bazel.build/docs/bazel-and-apple> | 2026-05-04 (Phase A carry-over; Bazel iOS adapter detection rule) | adapter detection |
| `bazel/rules_apple` | <https://github.com/bazelbuild/rules_apple> | 2026-05-04 (Phase A carry-over) | adapter detection |

## Verification protocol

Every URL added or used by a chat-N finding **must** appear in the table
above with a verification date. When Phase A+ adds a new citation:

1. `WebFetch` (or `curl -I` for SPA-style pages whose body is JSON-loaded
   after the shell HTML) the URL to confirm it resolves.
2. Paste a one-line confirmation note (with the date) into the table.
3. If the URL redirects, record the resolved URL and use that in the
   finding's `citation.url`.
4. If a URL 404s, do **not** use it. Find a stable replacement or mark
   the finding `UNVERIFIED — needs check` per the project rule.

When a citation is to a long page (release notes, transcripts), prefer
to also pull the relevant verbatim quote into
`references/build-settings-best-practices.md` and `grep -F` it against
the on-disk source so future chats have the quote without re-fetching.
