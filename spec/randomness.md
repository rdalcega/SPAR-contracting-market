# Randomness

This file fixes where the market's randomness comes from and what it may depend on. It states the keyed-draw
contract, then lists every draw the market makes: its key, the variates it takes, how its value is computed from them,
when it is made, and what the counters in its key count. What each drawn value then does (balances, deadlines, runs
arriving, rulings) belongs to the files named in each entry. This file owns the formulas that turn variates into
values; `processes.md` restates those of a run, a reading and the arbiter's reading ("The laws of a run", "The law",
"Dispute and the arbiter's ruling"), identically.

## 1. The keyed-draw contract

1. **Keys.** Every random value in the market comes from a *key*: a tuple of parts that starts with the episode's
   seed and names what the value is for (section 3 lists them all). A key determines a stream of independent
   variates (uniforms on [0, 1); section 2 builds standard normals, integers and permutations from them). The
   uniforms depend on the key and on nothing else: not on what agents did, on the order in which draws are made, on
   other keys' draws, on `message_channels`, on the models or on any parameter.
2. **Generator.** Which pseudo-random generator is used, and how a key seeds it, is the implementer's choice. It must
   be deterministic (the same key gives the same variates on every run, machine and process), two different keys
   must never be treated as the same key, and distinct keys must give streams that behave as independent. (For
   example, a generator seeded with the parts' text joined by `|`, such as `0|share|buyer|0`: not a requirement.) The
   implementation need not reproduce any particular numbers; it must reproduce which parts make up each key (and so
   which draws share variates and which do not), the laws and the constructions below. The literal parts in section
   3 (`"id"`, `"order"`, `employee`, ...) are names: any spelling is acceptable as long as distinct keys stay
   distinct.
3. **Draw-matching.** Two episodes with the same seed and the same market parameters (for example two arms
   differing only in `message_channels`, in the models or in the prompts) must get the same variates for every key
   they both use. So the last arrival day L (not the market's last day, which also depends on what agents do:
   `episode.md`, "The end of a day: the close rule"), the participants' IDs and wealth shares, every task's arrival
   day, worth and due day, and the turn order of every round both arms play are identical across such arms, and a
   run, a reading or a ruling that has the same key in two arms gets the same variates in both (and the same value
   whenever the inputs it depends on, such as the parent's quality of a refinement, are also the same).
4. **Counters.** Some keys contain a counter (`k`, `n`, `i` below). A counter counts only events that took effect in the
   market, as each entry defines. A refused action never advances a counter. Checking an action against any state other
   than the market's (for example the state the agent saw at the round's start; see `rounds.md`, "Running one action:
   the state its turn saw (R1)") must not change the market's counters; since a draw depends only on its key, such a
   check and the market's own run of the same action, with the same counter values, get the same values.
5. **Nothing else is random.** Everything in the market not listed in section 3 is deterministic: every tie-break, every
   ordering other than the turn order, every rule. No seed is sent to the models, and their replies are not part of the
   keyed draws, so two episodes of one seed differ wherever a model's reply differs (with every seat on the offline
   mock model of `running.md`, "The offline mock model", two episodes of the same seed and the same config whose
   programs give the same results are identical apart from wall-clock times and measured durations: `running.md`,
   "7.1 Requirements"). Randomness outside the market (waits between retries
   of a failed model call; programs agents run with the compute function, which may use randomness of their own) is
   not a market draw and must not touch the market's keys.
6. **Resume.** The counters of section 3 are part of the episode's state: per contract, worker and kind, the run
   count k; per contract, the label-draw count n (discarded draws included, so it can exceed the number of results);
   per contract, side and result, the reading count i; and the labels already used on each contract. A resumed
   episode (`running.md`, "Checkpoints and resuming") restores them, which is all it needs for every later draw to be
   what it would have been.

## 2. Variates and constructions

Where an entry says how a value is computed from its variates, that construction is required (it fixes how values
move together across arms and parameter values, beyond their law). Where an entry asks only for a law (a uniform
integer, a uniform permutation, the clipped standard normal), any exact method for that law is acceptable.

- **U**: a uniform variate on [0, 1).
- **Standard normal Z**: a variate with the law of Φ⁻¹(min(max(U, 1e-12), 1 − 1e-12)), Φ the standard normal CDF
  (that formula, one uniform per normal, is one acceptable method). This law is the standard normal with its tails
  clipped: a value below Φ⁻¹(1e-12) ≈ −7.03448 becomes Φ⁻¹(1e-12), and a value above Φ⁻¹(1 − 1e-12) ≈ 7.03449
  becomes Φ⁻¹(1 − 1e-12) (each with probability about 1e-12). This file and the others call Z "standard normal", and
  the laws they state for values made from Z (the truncated normal's, the cost multiplier's mean 1) are those of an
  unclipped Z; they hold up to this clipping. Example: a uniform of 1 − 1e-14 gives Z = 7.03449, not 7.65073, so a
  default manager-fresh run takes 30 days, not 37. A key that gives several normals gives independent ones; each
  entry names them (z_q, z_t, z_c).
- **Uniform integer on [a, b]** (both ends included): each integer with probability 1/(b − a + 1).
- **Uniform permutation** of a list: each order equally likely.
- **Truncated normal TN(mean, sd, Z)** on [0, 10], by inverse CDF:
  ```
  u = Φ(Z)          (the uniform that corresponds to Z)
  N = Normal(mean, sd);  a = F_N(0);  b = F_N(10)
  p = min(max(a + u × (b − a), 1e-15), 1 − 1e-15)
  TN = min(10, max(0, F_N⁻¹(p)))
  ```
  Its law is Normal(mean, sd) conditioned on [0, 10] (what agents are told as "redrawn if the quality falls outside
  [0, 10]"), up to the two clippings: Z's (above), and p's, which changes the value only when a + u × (b − a) falls
  below 1e-15 or above 1 − 1e-15, that is when [0, 10] lies far in N's tail. Deliberate: by inverse CDF, not by
  resampling, so the value is monotone in Z and a key gives the same rank of quality whatever the mean and sd.
- `round(x, n)` is the rounding defined in `README.md`, "Number formats and rounding".
- **Accuracy.** Φ, Φ⁻¹ (and so F_N, F_N⁻¹) and `exp` in these constructions are double-precision functions accurate
  to a few units in the last place of their value, Φ also deep in its lower tail (the same accuracy `prompts.md`,
  "Conventions for the field definitions", requires of Φ, `erfc` and `exp`); correct rounding is not required. The
  generator is the implementer's choice, so draws are not expected to match the reference implementation's bit for
  bit, and a value within a few units in the last place of a rounding point (a reading's decimal, a run's whole day
  count, a cost's cent) may fall on either side. TN's inverse CDF magnifies such differences where p is close to 1
  (F_N⁻¹ is steep there): a quality made from a Z near the upper clip can differ by a few times 1e-5 between correct
  implementations, and by up to about 3e-10 at Z = 5. This is accepted.

## 3. The draws

| # | draw | key (parts after the seed) | variates | made |
|---|---|---|---|---|
| 3.1 | participant ID | `"id"`, role, seat, attempt | one uniform integer on [0, 4095] | episode start |
| 3.2 | last arrival day L | `"horizon"` | a sequence of U | episode start |
| 3.3 | wealth share | `"share"`, role, seat | one U | episode start |
| 3.4 | a buyer's task count on a day | `"arrivals"`, day, buyer seat | a sequence of U | start of each day 2..L |
| 3.5 | a task's worth | `"worth"`, day, buyer seat, j | one uniform integer | when the task arrives (days 1..L) |
| 3.6 | a task's due-day offset D | task id, `"deadline"` | one uniform integer | when the task arrives |
| 3.7 | a round's turn order | `"order"`, day, round | one uniform permutation | at the start of each round |
| 3.8 | a run's quality, days and cost | stream key, worker, kind, k | three standard normals | when a run action reaches the balance check |
| 3.9 | a result's label | stream key, `"label"`, n | one uniform integer on [0, 4095] | when a run is accepted |
| 3.10 | a test reading | stream key, `"test"`, side, label, i | one standard normal | when a test is taken |
| 3.11 | the arbiter's reading | stream key, `"arbiter"`, label | one standard normal | when a dispute is ruled on |

Values of the parts: role is `buyer` or `manager`; seat is the participant's index within its role (0-based, in
creation order); day is the market day (1-based); round is the round of the day (1..`time.rounds_per_day`); worker is
`employee` (runs started with delegate_run) or `manager` (run_myself); kind is `fresh` or `refine`; side is `manager`
(test_result) or `buyer` (test_delivery); label is the result's label as shown (`R-` and 3 lowercase hex digits);
task id is `task-N`.

**The contract's stream key.** Draws 3.8 to 3.11 belong to a contract and are keyed by its stream key: `task-N` for the
first contract on task-N (contract-N), and `task-N-k` for the k-th contract on that task (contract-N-k, k ≥ 2, which
exists after its buyer ended k − 1 contracts on the task and posted it again; `actions-buyer.md`, "accept_bid" and
"end_contract"). So a first contract's draws do not depend on whether contracts on other tasks were ended, and a
repost's contract has draws of its own, not copies of the ended contract's.

### 3.1 Participant IDs

- Participants are created in this order: buyer seats 0, 1, ..., then manager seats 0, 1, ... (`episode.md`,
  "Participants, IDs and seat order").
- A participant's ID is its prefix (`B` for a buyer, `M` for a manager), `-`, and x written as 3 lowercase hex digits,
  zero-padded (x = 0 gives `000`), where x is the uniform integer on [0, 4095] of key (seed, `"id"`, role, seat,
  attempt), attempt = 0.
- If that ID equals the ID of a participant created earlier, it is drawn again with attempt = 1, 2, ... until it does
  not. (Only participants of the same role can collide, since the prefix differs; `B-abc` and `M-abc` may coexist.)
- Law: each seat's ID is uniform over the 4096 values of its role's prefix, distinct within the role.

### 3.2 The last arrival day L (the horizon)

- The cap is `time.horizon_min_days` + c, where c is the n of `config.md`, "6.1 The horizon cap" (the smallest
  integer ≥ 0 with (1 − `time.horizon_end_prob`)^c < 0.01; at the rare values within rounding error of a boundary
  either neighbour is accepted, as 6.1 says); a config whose cap exceeds 1000 is
  refused (`config.md`, "Checks across parameters"). Defaults: c = 44, cap 54.
- With u₁, u₂, ... the successive uniforms of key (seed, `"horizon"`):
  ```
  L = horizon_min_days
  for j = 1, 2, ...:
      if L = cap: stop
      if u_j < horizon_end_prob: stop
      L = L + 1
  ```
  One uniform is used per day considered beyond `horizon_min_days`, none once L reaches the cap.
- Law: P(L = min_days + j) = end_prob × (1 − end_prob)^j for 0 ≤ j < c, and the rest, (1 − end_prob)^c, at the cap.
  At the defaults L is 10 with probability 0.1, 10 + j with probability 0.1 × 0.9^j, and 54 with probability
  0.9^44 ≈ 0.0097.
- L is the last day on which tasks arrive; what it does is in `episode.md`, "The last arrival day L". It is never shown
  to agents.
- Example: uniforms 0.7543, 0.4056, 0.1340, 0.8446, 0.0525 (defaults) give L = 14.

### 3.3 Wealth shares

- For each participant: share = round(lo + (hi − lo) × U, `money.share_decimals`), with [lo, hi] =
  `money.share_range` and U the uniform of key (seed, `"share"`, role, seat).
- A share depends only on the seed, role and seat, so adding a seat leaves the others' shares unchanged.
- What the share sets (start balance, per-task budget) is in `episode.md`, "Wealth shares, start balances and
  per-task budgets".
- Example: U = 0.951777... at the defaults gives 0.6 + 0.8 × U = 1.3614..., share 1.36.

### 3.4 Task counts (arrivals)

- Day 1: every buyer gets exactly `tasks.first_day_tasks` tasks; no count is drawn.
- Days 2..L: for each buyer, with u₁, u₂, ... the successive uniforms of key (seed, `"arrivals"`, day, buyer seat),
  the count n is the number of uniforms below `tasks.arrival_p` before the first that is not:
  ```
  n = 0
  while u_(n+1) < arrival_p: n = n + 1
  ```
  Law: P(n) = (1 − p) × p^n for n = 0, 1, 2, ... (p = `tasks.arrival_p`; mean p / (1 − p), 1 at the default).
- Days after L: nothing arrives and nothing is drawn.
- Every buyer gets its count on every day 2..L, whatever its balance (the count depends on nothing but its key).

### 3.5 Task worths

- Each arriving task's worth is the uniform integer on [lo, hi] = `tasks.worth_range` (both included) of key
  (seed, `"worth"`, day, buyer seat, j), where j is the task's index among that buyer's tasks arriving that day
  (0-based; on day 1, j = 0 .. `tasks.first_day_tasks` − 1).

### 3.6 Due-day offsets D

- Each task's D is the uniform integer on [lo, hi] = `tasks.due_days_range` (both included) of key (seed, task id,
  `"deadline"`). Its due day is its arrival day + D, fixed when it arrives (`episode.md`, "Task arrivals").
- Task ids are numbered in order of arrival (`episode.md`, "Task arrivals"); since arrivals depend only on keys, a
  task's id, and so its D, are the same across arms with the same seed and parameters.

### 3.7 The turn order of a round

- At the start of round r of day d: the list of all participants in seat order (buyer seats 0, 1, ..., then manager
  seats 0, 1, ...), including participants out of business, is put in the order of a uniform permutation drawn from
  key (seed, `"order"`, d, r). What the order decides is in `rounds.md`, "Terms used in this file".
- Deliberate: the permutation is of all participants, so the relative order of those still in business does not
  depend on who went out of business.
- Keyed by day and round of the day, not by the round index g: day d's round r has the same order whatever
  `time.rounds_per_day` is, as long as that round exists.

### 3.8 A run's quality, days and cost

- Key: (seed, stream key, worker, kind, k), where k is the number of runs of that worker and kind already started on
  that contract (0 for the first). Its three standard normals are z_q, z_t and z_c. They are independent:
  a run's quality, days and cost are uncorrelated (a fixed rule, not a parameter).
- **Which runs count in k.** A run counts once it is started: it passed every check and its cost was charged
  (`actions-manager.md`, "delegate_run and run_myself"). A run refused for any reason (that section lists them),
  insufficient balance included, does not count. Runs
  that started count whatever happens to them later (the contract ended, the escrow closed, the market ended before
  they arrived). Fresh runs and refinements, and the employee's and the manager's runs, are counted separately; so
  are contracts.
- **When.** The draw is made when a run action has passed every check that comes before the balance check, because the
  balance check compares the balance with the drawn cost. Deliberate: a run refused for insufficient balance leaves
  k unchanged, so the next run of that worker and kind on that contract gets the same three variates, and so the same
  days and the same cost (the refusal's text states that cost; `actions-manager.md`, "delegate_run and run_myself").
  Its quality is computed then: a fresh run's is the same; a refinement's is computed from the parent that run names,
  so it is the same only if that parent has the same quality.
- With w = `production.employee` or `production.manager` for the worker:
  - fresh: q = TN(w.mu, w.sd, z_q); t = w.t_med.
  - refine of a parent result of the same contract, with true quality q_p (unrounded):
    q = TN(q_p + w.gain × (10 − q_p) / 10, w.sd_ref, z_q); t = w.t_ref_med.
    The variates do not depend on which result is refined; only the mean does.
  - days = max(1, ceil(t × exp(`production.sig_t` × z_t))), an integer ≥ 1. The run started on day s arrives at the
    start of day s + days (`processes.md`, "Arrival").
  - cost multiplier m = exp(`production.sig_c` × z_c − `production.sig_c`² / 2) (mean 1, up to the clipping of Z in
    section 2).
  - cost = round(C, 2) with C:

    | worker, kind | C | default (expected, m = 1) |
    |---|---|---|
    | employee, fresh | employee.fee + employee.cost_frac × cost_ref_worth × m | 1 + 3m (4.00) |
    | employee, refine | employee.fee + employee.ref_cost_frac × cost_ref_worth × m | 1 + 1.8m (2.80) |
    | manager, fresh | manager.k × cost_ref_worth × m | 8m (8.00) |
    | manager, refine | manager.ref_cost_fixed × m | 14m (14.00) |

    (`cost_ref_worth` is `production.cost_ref_worth`. The employee's fee is not multiplied by m.)
- q is kept unrounded; nobody sees it during the market.
- Examples at the defaults (outputs computed from the variates as shown):

  | run | z_q, z_t, z_c | q | t × exp(0.35 z_t) → days | m → cost |
  |---|---|---|---|---|
  | employee fresh | −0.0379, 1.0489, −0.3925 | 5.7649 | 1.4436 → 2 | 0.8786 → 3.64 |
  | manager fresh | 1.8723, −1.0971, 0.4217 | 9.1499 | 1.7029 → 2 | 1.0770 → 8.62 |
  | employee refine, parent q 5.7648 (mean 7.4589) | 0.4241, −3.1075, −1.0166 | 7.9737 | 0.2359 → 1 | 0.7517 → 2.35 |
  | manager refine, parent q 3.9116 (mean 8.1735) | −0.8212, 0.9382, −0.1958 | 7.5100 | 1.3887 → 2 | 0.9229 → 12.92 |

  The z's are shown to 4 decimals, so a q recomputed from them can differ in the 4th decimal. The employee-fresh row's
  result is the refine row's parent, and the manager-fresh row's the result read in 3.10's example; their unrounded
  qualities round to 5.7648 and 9.1498, the values those examples use as inputs. Refinements and readings use the
  unrounded quality.

### 3.9 Result labels

- Drawn once a run is accepted (after its balance check passed), before it is charged and recorded.
- Key: (seed, stream key, `"label"`, n), where n is the number of label draws already made on that contract,
  including draws discarded below (0 for the first).
- The label is `R-` and x written as 3 lowercase hex digits, zero-padded, x the uniform integer on [0, 4095]. If any
  result of the same contract (arrived or still running) already has that label, the draw is discarded and the next
  one (n + 1) is made, until the label is unused on the contract. Labels are unique within a contract only; two contracts may each hold an
  `R-baf`.
- A run is refused before any draw when its contract already holds 4096 results (`actions-manager.md`, "delegate_run and
  run_myself").
- Deliberate: the label is drawn from its own key, so it says nothing about the result's quality, worker, kind or
  order.

### 3.10 Test readings

- Key: (seed, stream key, `"test"`, side, label, i), where i is the number of readings that side already took of that
  result (0 for the first). The manager's readings (test_result) and the buyer's (test_delivery) are counted
  separately. A refused test takes no reading and leaves i unchanged.
- Drawn after the test's cost is charged (`actions-manager.md`, "test_result"; `actions-buyer.md`,
  "test_delivery").
- reading = round(min(10, max(0, q + sd × Z)), `tests.decimals`), with q the result's true quality (unrounded), Z the
  key's standard normal, and sd = `tests.manager_sd` (side manager) or `tests.buyer_sd` (side buyer). Clip first,
  then round.
- Example (manager, defaults): q = 9.1498, Z = −0.78218 → 9.1498 − 0.39109 = 8.7587 → 8.8.

### 3.11 The arbiter's reading

- Key: (seed, stream key, `"arbiter"`, label), label the disputed submission's result. No counter: a label is
  submitted at most once on a contract, so it is disputed at most once.
- reading = round(min(10, max(0, q + `contracts.escrow.arbiter_sd` × Z)), `tests.decimals`), q the result's true
  quality (unrounded).
- Made when the dispute is ruled on: at the start of the day after the dispute, or at settlement for a dispute still
  open when the market ends (`processes.md`, "Dispute and the arbiter's ruling", which also says what the reading
  decides).
- Example (defaults): q = 7.0219, Z = −1.15229 → 7.0219 − 0.57615 = 6.4458 → 6.4.

## 4. What the draws depend on (consequences)

- Fixed by the seed and the parameters alone, before any agent acts: L, every participant's ID and share, every
  task's arrival day, buyer, worth, D and due day, every round's turn order. Identical across `message_channels`
  modes, models and prompts for one seed.
- Changing the number of seats of a role leaves every other seat's ID, share and arrivals unchanged (an ID is checked
  only against participants created before it). Task ids, and so the D of tasks after the first affected arrival,
  can move, and so can every round's turn order (a permutation of a different list).
- A run's variates are those of its key, with k the number of runs of its worker and kind started before it on its
  contract's stream key, whatever happened before; a reading's are those of its key, with i the number of readings its
  side took of that label on that stream key before it. The same run or reading in two arms of one seed draws the
  same variates even when it happens on another day or round.
