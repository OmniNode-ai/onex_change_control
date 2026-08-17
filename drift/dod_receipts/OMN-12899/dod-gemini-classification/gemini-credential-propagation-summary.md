# OMN-12899 Gemini Credential Classification

Credential depletion / old-key mismatch: resolved.

Evidence captured during the OMN-12890 Gemini propagation check:

- Local and stability-lane runtime GEMINI_API_KEY hashes matched.
- Runtime containers exposed matching GEMINI_API_KEY and GOOGLE_API_KEY hashes.
- Direct Gemini API probes returned 200 OK from the local host, the stability host, and the stability runtime container.
- The remaining blocker was a Gemini OpenAI-compatible HTTP 400 after local tiers refused connection.

OMN-12899 makes that remaining provider HTTP 400 diagnosable by preserving a bounded, sanitized provider response body in failed delegation inference responses.
