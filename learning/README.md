# Learning loop tools

These tools read finished episodes. None of them changes the market.

| Module | Role |
|---|---|
| `logs.py` | Loads an episode folder (`config.json`, `log.jsonl`, `summary.json`). Indexes each record type and keeps any unknown types, which it lists in `Episode.unknown_types`, so a changed market still loads. |
| `firm.py` | Decides what one firm (a seat) may see: the information firewall. It also computes the firm's profit on each contract and reconciles its score. |
| `anonymize.py` | Replaces participant IDs with labels that hold within one episode ("you", "Buyer 1", "Manager 2"). |
| `digest.py` | Builds the text a firm's writer reads for one episode. |
| `objective.py` | The arms, and what each optimises, defined once for acceptance, the digest and the writer. The main arms other than welfare use the agent's own score. The welfare arm uses the market's score: total gain of all participants, with every credit lost counted twice. The extra weight on losses is `loss_weight`, default 1. |
| `measures.py` | Computes the outcome measures of one episode from its log (definitions in its docstring). |
| `hook.py` | Adds a firm's instructions to its seat's system prompt. It wraps the market's prompt builder instead of editing it. With nothing registered, prompts stay byte-identical. It stands in for a per-seat config parameter until Rodrigo agrees one. |
| `design.py` | The 8×8 Hadamard assignment table, the second play's flip, and the seed ranges (training seeds from 10,000; test seeds 5000–5011; never 0–5). |
| `accept.py` | The paired comparison per firm and the keep-or-drop rule for each arm (compete, sham and welfare, the main arms with messages on; compete_no_messages, an ablation with messages off). |
| `episodes.py` | Runs a generation's episodes through the market's own `run_episode`, each under its own run name with its firms' instructions. |
| `writer.py` | Builds the writer's prompt, reads its reply, strips IDs, enforces the length cap and retries once. It includes a real-model writer through the market's model layer and a free test writer. |
| `lineage.py` | The run's state: every version of every firm's instructions, decisions and digest pools. It is saved after every step. |
| `loop.py` | `python -m learning.loop SPEC.yaml` runs or resumes one run: a starting batch, then rounds of write, play and decide. |

## What a firm may see

The full rules are in `firm.py`. Only the fields listed in `VISIBLE` are ever copied.

**Always visible:**
- its own reasoning, reply text, memory, actions and results;
- notifications sent to it;
- the public facts of its own contracts;
- payments it made or received;
- its own test readings;
- messages it sent or received.

**After the episode:** its own score, its money by kind, the market's length, and the number of tasks spawned.

**By role:**
- *Buyers* see the value each of their contracts realised.
- *Managers* see their own runs, but never true quality, and never a buyer's realised value.

**Never visible:** other firms' scores, reasoning, memory or notifications; the last arrival day; the close reason; market metrics; random draws.

## The digest

The digest has these sections, in order:
1. **Outcome:** score, score per task and money by kind.
2. **Contracts:** one entry per contract, with the firm's profit on each.
3. **Memory:** up to 10 evenly spaced memory writes.
4. **Reasoning:** for the 2 most and 2 least profitable contracts, the lines of the agent's reasoning that both mention the contract and read as deliberation.
   - *A mention* is the contract's ID, its post, its task or its result labels, in any case, with a space, "#" or a plural ("Contract 12", "contracts 12"). A number never matches inside a longer one.
   - *Deliberation* means the line is in the first person, or states an intention, a need or a reason.
   - *Repeats are dropped:* a line already shown for that contract isn't shown again, ignoring day countdowns.
   - *Caps:* each turn keeps its last whole lines up to 1,500 characters, and each contract up to 6,000 characters.
   - *Why:* real Qwen reasoning is mostly status lists naming every contract. Whole paragraphs repeated those lists under each contract. Status lines repeat what the ledger already shows.
5. **Messages:** up to 20 evenly spaced messages, only when there are any.

**Size:** each digest is capped at 48,000 characters. When it's too long, trimming cuts the reasoning first, then memory, then messages. The outcome and contract sections are never cut.

**Neutrality:** every selection rule depends on time, size or the firm's own profit, never on the kind of action, so the digest doesn't point the writer at any behaviour.

## Tests

The tests are in `tests/learning/` and run on free mock episodes. A test-only "rich" mock adds rejections, disputes, stated qualities, payments outside contracts, ended contracts, reasoning text and token charges.

They check that:
- every log loads;
- every score reconciles, both from money and from contract profits;
- the two parties' profits on a contract net out;
- planted sentinels in forbidden fields never reach firm data or digests;
- no IDs survive anonymising;
- digests are deterministic and within the size cap;
- reasoning appears only for the selected contracts;
- excerpts hold only paragraphs about their own contract.

They also check:
- **Measures:** known answers on small hand-made logs (non-payment when able, the claim gap, untested claims, knowing submissions, disputes against evidence, the message screen), and that the rich mock produces each case.
- **The loop:** the prompt hook is byte-identical with nothing set; the table is exactly balanced and flips; seed ranges are separate; each arm's acceptance arithmetic is right; the sham coin is reproducible and drawn per firm; the writer reads, strips IDs, enforces the cap and retries; the run state round-trips.
- **A full dry run:** two generations with a resume in between. The mock ignores instructions, so every paired difference is exactly 0. That confirms the pairing removes everything except the instructions. The instructions reach exactly the seats the table assigns.

**Real logs:** `test_learning_real_logs.py` runs on real episodes when `LEARNING_REAL_LOGS` points at a folder of them. It checks:
- every real episode loads, with no unknown record types;
- every score reconciles;
- digests fit the size cap, hold no IDs, and list contracts in the order they were made;
- every excerpt line is deliberation that mentions its contract and is never repeated;
- every measure computes.

It passes on 32 Qwen3.5-9B episodes: 2 repetitions of seeds 12–19, messages off and all.

**Still open:** Haiku logs (its reasoning is an API summary, not the raw trace), and logs with the final configuration.
