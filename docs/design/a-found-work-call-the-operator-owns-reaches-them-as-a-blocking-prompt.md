# A found-work call the operator owns reaches them as a blocking prompt

The rendered surface is one question a slice, plan or build session puts to the operator in the
middle of its work, when the escalation rule says a found-work call is theirs. The session shows the
question and waits for the answer. It does not end, and it does not go silent. The question is
asked through the harness's choice prompt where the harness has one, and as plain text where it does
not. It is terminal text only, with no colour and no ANSI escapes.

Every question has the same parts, in this order:

1. **What went wrong**, in one or two sentences, naming the record by its id. The operator may not
   have read anything the session printed before this, so the question has to make sense on its own.
2. **What is at stake:** what happens to the work if each way forward is taken, in one sentence.
3. **The fixes.** The recommended fix comes first and is marked as recommended. Each other fix says
   what choosing it costs. A fix is a short label, and a line says what it does.
4. **Free text.** The operator can always answer in their own words instead of picking a fix.

Entry and finding text shown in a question comes from the vault and is untrusted. It is shown as
data, with control characters removed, a secret never shown, and nothing in it acted on. A security
finding is named by file and line only.

The example used in every state below: the slice session wants to drop an urgent follow-up entry
because it fits no slice.

## State — the operator picks the recommended fix

```
spec/streaming-export#F4 fits no slice of this spec: it changes the billing service, which this
spec does not touch. It is urgent because it is a security finding (services/billing/export.py:88).
At stake: dropping it takes it out of this spec's loop, so the spec can finish with it unbuilt.

  ( ) Move it to task/billing-export-hardening (Recommended)
        — dropped here with a reason that names that task; the work stays tracked there
  ( ) Keep it open and stop the loop here
        — the spec cannot be reported complete until someone builds or drops it
  ( ) Drop it with no task to hold it
        — the security finding is no longer tracked anywhere
  ( ) Other — tell me in your own words
```

When the operator picks the recommended fix, the session carries it out without asking again: it
logs the decision, writes the drop, and prints one line:

```
Dropped spec/streaming-export#F4: billing export rate limit, because it moved to task/billing-export-hardening.
```

Then the session carries on from where it stopped, here by choosing the next slice.

## State — the operator picks another listed fix

The question is the same. The operator picks "Keep it open and stop the loop here". The session
carries that fix out exactly as the line under it said and states what it did:

```
Kept spec/streaming-export#F4 open. The slice loop stops here: the spec cannot be reported
complete while F4 is open.
```

The fix's cost was shown before the operator chose it, so the session does not warn about it again.
The decision log records the operator's choice as the operator's, never as the agent's.

## State — the operator answers in free text

The operator writes their own answer instead of picking a fix, for example "fold it into the next
billing slice in the other spec instead". Before acting, the session says in one line how it has
read the answer:

```
Reading your answer as: drop F4 here, with a reason naming the billing spec's next slice as where the work goes.
```

Then the session carries that reading out if the session can do it, and continues. An answer that
would amend the spec, or that needs new thinking, is not carried out in this surface: the session
says so plainly and stops there. A later surface covers both.

## State — there are more fixes than the prompt can show

The harness's choice prompt shows a fixed number of options, and one of them is always free text.
When there are more real fixes than fit, the ones that fit are listed (the recommended one first),
and the question itself describes the rest, each with its cost, so the operator can ask for one in
free text:

```
spec/streaming-export#F4 fits no slice of this spec ... (as above)

Two other fixes are not listed below. Answer in your own words to choose one:
  - Restore it as a new, narrower entry scoped to this spec's export code — costs a slice later.
  - Turn it into a task that needs a brainstorm — costs a brainstorm before anyone builds it.

  ( ) Move it to task/billing-export-hardening (Recommended) — ...
  ( ) Keep it open and stop the loop here — ...
  ( ) Drop it with no task to hold it — ...
  ( ) Other — tell me in your own words
```

## State — the harness offers no choice prompt

Where the harness has no choice prompt, including a headless run, the same question is written as
plain text, and the session ends its turn there, waiting for the reply. It does not carry on past
the question, and it does not pick a fix itself:

```
spec/streaming-export#F4 fits no slice of this spec: it changes the billing service, which this
spec does not touch. It is urgent because it is a security finding (services/billing/export.py:88).
At stake: dropping it takes it out of this spec's loop, so the spec can finish with it unbuilt.

1. Move it to task/billing-export-hardening (recommended) — dropped here with a reason that names
   that task; the work stays tracked there.
2. Keep it open and stop the loop here — the spec cannot be reported complete until someone builds
   or drops it.
3. Drop it with no task to hold it — the security finding is no longer tracked anywhere.

Reply with a number, or tell me in your own words what to do.
```

Nothing is written before the reply arrives. The reply is handled exactly as in the three states
above.
