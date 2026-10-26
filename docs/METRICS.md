# How /hevy calculates things (analytics version 1)

Every number on the site comes from the rules below. They are applied to the locally stored Hevy history, and
`python -m hevy.cli rebuild-analytics` re-applies them from scratch. When a rule changes, `ANALYTICS_VERSION` in
`hevy/analytics/rules.py` goes up and everything is recalculated. These texts are also the source for the in-app
"how is this calculated?" explanations.

## Days, weeks, time

* A workout belongs to the **local date it started** (Europe/London, so BST is handled). Weeks run Monday to Sunday.
* **Training time** is the duration as logged in Hevy (end minus start). Nothing is capped or guessed.
* **Gym session vs cardio session.** A workout is a *cardio session* when every exercise in it is cardio (Hevy's
  muscle group "cardio", or a distance/duration exercise such as Walking or Treadmill). Anything else is a *gym
  session*, even if it includes some cardio. This matters because walking days are often logged as one long
  "workout" (several hours, many walking sets) and would otherwise dominate time totals and session counts.
  Totals are shown for both, and per-minute figures use gym sessions only.

## Sets

* **Working set:** set type normal, failure or drop set. **Warm-ups** are kept and shown but never count as
  working sets, volume, estimated 1RM or records.
* **How an exercise is measured** (from its Hevy type):

| Hevy type | Measured as | Counts towards |
|---|---|---|
| weight_reps | load × reps | volume, e1RM, all strength records |
| bodyweight_weighted (e.g. weighted hyperextension) | added load × reps | volume (added load only), load and rep records; no e1RM (bodyweight unknown at the time) |
| bodyweight_assisted_reps | reps (the weight is assistance) | reps, rep records |
| reps_only, bodyweight_reps | reps | reps, rep records |
| duration (planks, hangs) | seconds | time, longest-hold record |
| weight_duration | load and seconds | load and time records |
| short_distance_weight (sled, farmer's walk) | load and distance | heaviest-load and distance records |
| distance_duration (walking, treadmill) | distance and time | cardio distance and time, longest-session records |
| floors_duration (stair machine) | floors and time | cardio time |
| anything else | inferred from which fields are filled in | |

## Volume

**Volume = Σ load (kg) × reps over working sets of load-and-reps exercises.** It counts external load only (bar,
dumbbells, machine stack, added weight). Bodyweight is not included, so a set of push-ups adds reps but no volume.
Cardio, holds and carries have their own totals (distance, time) and never mix into volume.

## Estimated 1RM (e1RM)

**Epley formula: e1RM = load × (1 + reps ÷ 30)**, and for a single rep the load itself. It is a widely used,
published formula; it is an *estimate* of the most you could lift once, not a measurement.

* Only working sets of load-and-reps exercises with **1 to 12 reps** get an e1RM. Above 12 reps the estimate is
  unreliable, so those sets are kept but marked "e1RM not estimated (too many reps)".
* An exercise's e1RM for a session is the best e1RM of any eligible set in it.
* For comparison, Brzycki (load × 36 ÷ (37 − reps)) gives similar values up to about 10 reps.

## Suspect sets (probable typos)

A working set is marked **suspect** when it is far outside what you normally log for that exercise (needs at least
5 earlier or later working sets of the exercise to judge):

* reps more than 4 × the exercise's median reps and more than 40 (e.g. 12 kg × 120), or
* load more than 3 × the exercise's median load and more than 50 kg.

Suspect sets stay in the history and in set counts, but are **left out of volume, e1RM and records**, and listed under
System → Data notes so they can be corrected in Hevy (the correction syncs back automatically).

## Records (PRs)

Records are tracked per exercise and per category, in the order the sets were done. A record needs to be *strictly
better* than everything before it; one record per category per session (the session's best).

| Category | Applies to | Meaning |
|---|---|---|
| e1RM | load × reps | best estimated 1RM (sets of 1 to 12 reps) |
| Heaviest weight | load × reps, added load, load × distance, load × time | most load in a working set (any reps) |
| Rep record at weight | load × reps, added load | more reps than ever before **at that load or heavier** |
| Best set volume | load × reps | load × reps of a single set |
| Best session volume | load × reps | the exercise's total volume in one workout |
| Most reps | reps-only exercises | most reps in a single set |
| Longest hold | duration exercises | longest single set |
| Longest distance / longest session | cardio | most distance / time for the exercise in one workout |
| Biggest workout | whole workout | the workout's total volume |

* The **first session** of an exercise sets the baseline: nothing to beat yet, so no records.
* Records in an exercise's **first three sessions** are marked *early*. Fast early gains are mostly learning the
  movement and finding working weights.
* A record is **meaningful** when it is not early and improves on the previous best by at least 1% (load and volume
  categories) or at least one rep / one second / one metre. Others are still listed but shown quietly.

## Muscles

Hevy gives each exercise one primary muscle group and optional secondary groups.

* A working set credits **1 set to the primary muscle and ½ set to each secondary muscle**. Volume is credited the same
  way (100% / 50%). This is a convention to show where effort goes, not physiology.
* "Sessions" for a muscle counts workouts in which it received any credit.
* Hevy's groups "cardio", "full_body" and "other" are reported separately and are not drawn on the body map.

## Comparing periods

* Periods: last 7 / 28 days, 3 / 6 / 12 months, this week, this month, all time, or custom dates. The previous period
  is the same length immediately before ("this week" and "this month" compare with the same number of days at the
  start of the previous week or month).
* A change is only shown when **both periods have at least 2 gym sessions**; otherwise "not enough data to compare".
* **Strength change** between two periods = the **median** change in best e1RM across the exercises that have an e1RM in
  both periods. It needs at least 3 such exercises and the list of exercises used is always available. Each
  exercise's change is best e1RM in this period ÷ best e1RM in the previous period − 1.
* **Most improved** = the exercise with the largest strength change among those done at least twice in both periods.
* **Trained less than usual** (a muscle): over the 12 weeks before the period it averaged at least 2 credited sets a
  week, and in this period its weekly rate is below half of that. This is a description of your own pattern, not a
  recommendation.

## Trends

An exercise's **trend** is the robust (Theil–Sen) slope of its session-best e1RM over its last 8 sessions, as % per
month of the median e1RM. It needs at least 4 sessions spanning at least 3 weeks, otherwise "not enough data".
Theil–Sen takes the median of the slopes between every pair of sessions, so one unusual session barely moves it.

## Streaks and frequency

* A **training week** has at least one workout (gym or cardio). The streak counts consecutive training weeks up to the
  current week (the current week doesn't break the streak until it is over).
* Gym-session streaks are counted the same way using gym sessions only.

## Plateaus (per exercise with an estimated 1RM)

Looks at the exercise's **last 8 sessions that have an e1RM** (at least 6 are needed; otherwise "insufficient data").

| Status | Rule |
|---|---|
| Improving | e1RM trend (Theil–Sen, see Trends) at least **+1% a month** |
| Declining | e1RM trend **−1% a month** or worse |
| Productive plateau | flat trend, **no new best e1RM for 6+ sessions**, but session volume or reps up **at least 5%** (last half of the window vs the first half) |
| Stalled | flat trend, no new best for 6+ sessions, and volume and reps not rising |
| Stable | flat trend, but a best was set within the last 5 sessions |

Every status lists its reasons: the trend, sessions since the best, and the first-half vs second-half change in volume,
reps, top load and sets. Exercises last done more than 90 days before your latest workout are marked as not current.

## Momentum

Momentum is judged **as of your latest workout**, for exercises done within 90 days of it.

* **New**: first done within 6 weeks of the latest workout.
* **Accelerating**: improving, a best within the last 2 sessions, and the last 4 sessions rising at least 1% a month
  faster than the last 8.
* **Improving / declining**: as in Plateaus.
* **Maintaining**: stable or productive plateau.
* **Plateauing**: stalled.

Muscles: credited sets a week over the **last 4 weeks vs the 8 weeks before** (to the latest workout). Rising: up 25%
or more (or new). Falling: down 25% or more. Otherwise steady. Muscles under 1 set a week in both are left out.

## Exercise order effect

Does an exercise go worse when it comes later in the workout?

1. Each session's best e1RM is compared with the exercise's **level at that time**: a straight line fitted through
   its other sessions within ±8 weeks (at least 4 needed), evaluated on that day. This removes steady progress or
   decline over time, which would otherwise look like an order effect whenever the exercise moved earlier or later
   in your workouts.
2. Sessions are grouped: **exercise 1–2** vs **exercise 4 or later** (3rd is left out to keep the groups distinct).
   Each group needs **at least 5 sessions**.
3. Effect = average relative e1RM late ÷ average relative e1RM early − 1.
4. A **90% interval** comes from 2,000 bootstrap resamples (fixed seed, so it is reproducible). The effect is called
   **clear** only when that interval doesn't include zero; otherwise it is "within normal variation".

This is an observed pattern in your history, not proof of cause. The raw average e1RM in each group is shown too, but
it is not the effect.

## Training DNA (any period)

Shares of working sets unless stated:

* **Rep ranges** (sets with load and reps): heavy 1–5, moderate 6–12, high 13+.
* **Failure** and **drop sets**: set types logged in Hevy.
* **Multi-muscle exercises**: exercises Hevy lists with at least one secondary muscle. A stand-in for "compound",
  which Hevy doesn't record.
* **Equipment**: free weights (barbell, dumbbell, kettlebell, plate), machines and cables, bodyweight and bands.
* **Cardio sessions**: share of workouts that are cardio sessions.
* Facts: median gym-session length, exercises used, exercises done for the first time ever, average RPE (where logged)
  and how often RPE was logged.
* **Regions** (credited sets): upper body, lower body, core (abs and lower back).

DNA is shown for any period and can be compared with another (for example October 2025 vs October 2026). Under 30
working sets the page says the sample is small.

## Muscle balance

Descriptive splits of credited sets, never a judgement: push (chest, shoulders, triceps) vs pull (lats, upper back,
biceps); chest vs back (lats + upper back); quads vs hamstrings; biceps vs triceps; upper vs lower body; front vs back of
the body (front: chest, abs, quads, biceps, shoulders, adductors; back: upper back, lats, lower back, glutes, hamstrings,
calves, triceps, traps, abductors). No "ideal ratio" is applied.

## Similar workouts and comparisons

* **Similar**: same Hevy routine, or at least half of the exercises in common (Jaccard similarity ≥ 0.5), and the
  same kind (gym or cardio).
* **Compare two workouts**: totals side by side with % change (second vs first), exercises matched by exercise
  (e1RM and volume change where both have them), and credited sets per muscle.

## Next-session estimate (experimental)

**Chance of a new best e1RM next time = how often that happened in your own history in the same situation.** The
situation is how far the last session was below the exercise's best e1RM (within 2%, 2–5%, 5–10%, 10% or more) and
whether the trend over the last 8 sessions was rising. Cases come from every exercise (sessions after a gap of more than
60 days are left out). If a situation has fewer than 30 cases, it falls back to the distance band alone. At least 8
sessions with an e1RM are needed.

* **Why frequencies:** a first model projected the trend line and assumed normal noise. Tested on this history it was
  badly overconfident (it said 90% when new bests happened under 20% of the time) and scored worse than always
  guessing the average rate, so it was rejected.
* **Checked against your history:** every exercise is predicted from the *other* exercises' cases only, at every past
  session. The calibration bins (predicted vs what happened) and the Brier score, compared with always guessing the base
  rate, are shown with the estimate. On the history it was developed on, it was well calibrated but only modestly informative (a Brier
  score a little better than always guessing the base rate), and the page says so.
* **Also shown:** what the trend points to (the median of the last 3 sessions plus the trend over the usual gap) as a
  set at your typical rep count, and **the set that would beat your best**. Loads are rounded to the step you usually use
  for that exercise. Confidence is "low" under 100 cases or after more than 60 days away.

## Year in Training (Wrapped) and landmarks

Any period. Cards are plain facts: totals, the highest estimated 1RM, the biggest meaningful e1RM record (largest %
improvement), the most-done gym exercise and cardio, the most-trained muscle (credited sets), the most active month,
the usual weekday and time of day of gym sessions (morning 5–12, afternoon 12–17, evening 17–22, night), exercises done
for the first time ever, the longest run of consecutive training weeks, the longest gym session, the biggest workout
by volume, the biggest progression, and the top Training DNA traits.

* **Biggest progression:** among exercises with 6+ e1RM sessions in the period, the median of the last 3 sessions' best
  e1RM vs the median of the first 3.
* **Landmarks:** total volume ÷ an approximate reference mass, and cardio distance ÷ a reference distance. Only
  comparisons giving between 1 and 100,000 of something are used, and they rotate. The division is exact; the reference
  figures are rounded public approximations (e.g. adult African elephant about 6 t, London double-decker bus about 12.5 t,
  blue whale about 150 t, Eiffel Tower iron structure about 7,300 t; marathon 42.195 km). They are for fun.

## Exports

CSV (one dataset as a file, several as a ZIP), JSON or Excel. Datasets: sets, workouts, exercises, records, days, and the
raw Hevy payloads. Filters: date range ("all time" is never capped), exercise, muscle (any credit), workout. Each export
includes an About section with the filters, the rules version and the app version. Text cells that a spreadsheet
could run as a formula are neutralised.

## Data explorer

Any slice of your sets. Filters: dates, exercises, muscle (any credit, or main muscle only), gym or cardio, set type
(working by default), weight, reps, RPE, workout length. Suspect sets are excluded unless asked for. Shows totals, reps
and weight distributions, sets per week, weight vs reps (thinned evenly to at most 2,000 points), and the rows (up to 500
a page, sortable).

## Strength Index

A personal composite of how strong you are **relative to your own starting points**. It is not an absolute
measure and can't be compared between people.

1. **Components:** up to 8 exercises with an estimated 1RM, the most-done ones with at least 8 such sessions, and
   at most 2 per main muscle so one body part can't dominate. They are chosen automatically and listed with their
   start and current levels.
2. **Level** of a component in a week = the median of its last 3 sessions' best e1RM up to then. It is **active** while
   it was done within the last 90 days.
3. **Chain-linked:** the index starts at 100. Each week it moves by the geometric mean of the week-on-week change in
   level of the components active in both weeks (at least 3 needed; otherwise it holds). This is how price indices
   handle changing baskets: an exercise starting, stopping or coming back can't make it jump; only real changes in
   level move it.

Shown with its change over 13 and 52 weeks, its peak, and every component.

### Wrapped periods and scaled comparisons

* Periods: this year so far (the default, or last year in January and February), last year, last month, last 3 or 12
  months, last 5 or 10 years, all time, any calendar year, or custom dates. Wrapped is computed live each time.
* If the period starts more than a month before your Hevy history, the page says when your history starts and how much
  of the period it covers. "Per year" figures use only the covered part.
* Comparisons are chosen so the count is readable (between 1.5 and 60, closest to about 6 first): animals for a month,
  aircraft for a year, rockets and towers for a decade. "Other comparisons" rotates through the readable ones.
  For a year or more, "at this pace" gives how long the impossible would take (the Great Pyramid, Lake Windermere's
  water, the Moon), using your yearly rate over the covered period.
* Cards adapt: a busiest week instead of a busiest month for periods up to about two months; a year-by-year card and
  the best year (by volume) for periods of about two years or more; and a comparison with the period before, using
  the same minimum-sample rule as everywhere else.

## Next workout

The homepage suggests the next one or two workouts, from the programme chosen on the System page only (a list of
Hevy routines; until one is chosen, the newest version of each routine in the folder of the routine done most
recently). A new suggestion is made after a sync brings a new or changed workout, when "Recalculate" is pressed,
after settings change and at least every 3 days. Automatic attempts are at least 10 minutes apart.

**Built-in rules** (always computed; the fallback):
- *Routine family*: the title without a trailing version ("Deadlift Day v3.1" and "Deadlift Day v3" are one family),
  so history from older versions counts.
- *Due*: (gym sessions since the family was last done + 1) / (gym sessions since the programme started / times the
  family was done). 1.0 means it's on its usual rhythm. Counting sessions rather than days keeps the order sensible
  after a break. A routine never done scores 0.5.
- *Recovery*: the share of the routine's planned working sets whose primary muscle already had that many credited sets
  in the last 48 hours (today and yesterday) reduces the score by up to 70%.
- *Cardio*: an all-cardio routine counts half when the last 7 days already hold 90+ minutes of cardio.
- *Targets per exercise* (double progression on the routine's planned sets): if the last session matched or beat every
  planned set's reps at or above the planned top weight, add one load step (the exercise's usual increment, as in the
  next-session estimate); otherwise repeat the last top weight (or the plan's, if last time was more than 10% lighter).
  After more than 14 days off that exercise, ease back about 10%, rounded down to a step. Never more than the heaviest
  ever plus one step. Sets without load (cardio, reps only, timed) are kept as planned.

**Your GPT in ChatGPT** (docs/CHATGPT.md): when asked in ChatGPT, it reads the same data as the OpenAI coach below
and posts its pick, which goes through the same checks. A ChatGPT pick stays on the homepage until a new workout
syncs; then the rules take over until it's asked again.

**OpenAI API** (when an API key is installed): it gets the programme, the last 21 days, recent muscle work, the last
performance of each exercise (with its step and heaviest load), the rules' proposal and your instructions, and
must answer in a fixed JSON schema. The reply is checked before use: routines must be in the programme (at most two,
no repeats), exercises must belong to the routine and cover at least half of it, and numbers must be plausible
(load 0 to 1.3× the larger of planned and heaviest-ever, plus 5 kg; reps 1 to 100; 1 to 12 sets). An exercise with
implausible numbers gets the rules' targets; a reply that fails otherwise is replaced by the rules' answer, and the
reason is shown. After a temporary OpenAI failure it is tried again after an hour.
