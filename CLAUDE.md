# Contracting market

This repository implements the market specified in spec/, built from the spec alone.

- spec/ is the requirement. Start with spec/README.md: what "exactly the same market" means, the number
  formats, the vocabulary and the acceptance checks. Do not edit spec/; it is a snapshot of the reference
  market (SPAR-pilot-run, tag handover/market_v19).
- Build blind: do not read other folders of this workspace, in particular ../SPAR-pilot-run (the reference
  implementation). The spec, its examples and its vectors are the only input.
- Where the spec seems wrong, incomplete or ambiguous, do not guess silently: add the question to
  SPEC_QUESTIONS.md (the section, a quote, the case that shows it), go on with the reading you would defend,
  and let the user decide.
- Lean: the simplest code that meets the spec. No switch except message_channels; nothing the spec does not
  describe.
- Done means the six acceptance checks of spec/README.md pass, with tests of your own beside them.
- Never call a paid model without asking first, and keep cost_cap_usd set. The mock provider is free: use it
  for every test and smoke run. Keys come from the environment (OPENROUTER_API_KEY) or a git-ignored .env, never from committed files.
