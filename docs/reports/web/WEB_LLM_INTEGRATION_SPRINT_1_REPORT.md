# Web LLM Integration Sprint 1 Report

**Backend baseline:** Financial Eligibility Engine v0.4.6  
**Web baseline:** v0.4.6 Web UX5  
**Scope:** native LLM connection only; no financial-core semantic changes

## 1. Result

The Web MVP now has two explicit external LLM routes:

```text
LLM_PROVIDER=OPENAI
→ OpenAI Responses API (/v1/responses)
→ strict Structured Outputs
→ IntentParser / ConversationOrchestrator / QuestionGenerator / ResultExplainer
→ ApplicationService
→ deterministic Financial Eligibility Engine

LLM_PROVIDER=OPENAI_COMPATIBLE
→ Chat Completions (/v1/chat/completions)
→ existing compatible adapter
→ same ApplicationService
```

`OPENAI` no longer aliases the generic Chat Completions adapter.

## 2. Direct OpenAI defaults

```text
LLM_PROVIDER=OPENAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-5.6-terra
LLM_REASONING_EFFORT=low
```

Credential lookup:

```text
LLM_API_KEY
→ fallback OPENAI_API_KEY
```

The key is never hard-coded and `.env` is ignored by git.

## 3. Structured Outputs hardening

A transport-level JSON Schema normalizer was added for OpenAI strict Structured Outputs.

- removes Pydantic `default` annotations from provider schema
- marks every declared object property as required
- forces `additionalProperties=false`
- preserves optionality through `null` unions
- final response is still validated by the original Pydantic model

The previous unconstrained `Any` fields on the conversational LLM boundary were narrowed:

- `HardConstraint.expected`
- `Preference.expected`
- `ConversationAction.new_value`
- `ConversationAction.answer`

Active-question resolution objects now use a typed `ConversationStructuredAnswer`; ApplicationService converts that model back to the existing deterministic answer payload before validation.

## 4. Privacy / safety boundary

Direct OpenAI requests use:

```json
{"store": false}
```

The existing audit payload policy remains unchanged. API keys and authorization headers are not written to audit logs.

The model still cannot decide:

```text
eligibility status
Rule status
rate
interest
ranking
Top 5
```

It returns only typed natural-language interpretation / wording outputs that the deterministic ApplicationService validates and executes.

## 5. Runtime observability

New endpoint:

```text
GET /api/llm/health
```

It distinguishes:

```text
configured + healthy
configured + provider unreachable/error
not configured (for example missing API key)
MOCK/demo mode
```

The Web header now reports actual health rather than declaring an LLM connected merely because a provider name was configured.

## 6. Missing-credential behavior

Starting with:

```text
LLM_PROVIDER=OPENAI
```

but no `LLM_API_KEY` / `OPENAI_API_KEY` no longer crashes the Web process. Runtime starts in a safe unconfigured state and `/api/llm/health` explains the missing credential.

## 7. Tests added

- OpenAI provider defaults and `OPENAI_API_KEY` fallback
- provider selection maps `OPENAI` to the native Responses adapter
- `/v1/responses` structured-output payload and response parsing
- `store=false`
- strict schema normalization
- conversational schema contains no untyped `Any` nodes
- missing-key Web runtime safety
- native Responses adapter → ConversationOrchestrator → ApplicationService → REST end-to-end synthetic flow

## 8. Verification

```text
pytest: 374 passed
python compileall: PASS
node --check app.js: PASS
wheel build (--no-build-isolation): PASS
```

The standard `python -m build` command was unavailable in this execution environment because the `build` package is not installed. A PEP 517 wheel was successfully produced with the already-installed build backend using `pip wheel --no-build-isolation`.

## 9. Live-provider status

A real OpenAI request was **not** sent in this environment because neither `LLM_API_KEY` nor `OPENAI_API_KEY` is present. This is the only remaining blocker to a live handshake and real-model adversarial conversation test.

Once a credential is injected securely, verify:

```bash
export LLM_PROVIDER=OPENAI
export LLM_MODEL=gpt-5.6-terra
export OPENAI_API_KEY='...'
eligibility-web
```

Then check:

```text
GET /api/llm/health
```

and run the natural-language acceptance sequence:

```text
월 20만원으로 바꿀게.
급여계좌는 바꿀 수 있어.
생각해보니 잘 모르겠어.
카카오는 3천원으로 할게.
카카오는 빼줘.
아까 카카오 뺀 건 취소.
질문은 그만하고 지금 결과 보여줘.
금리 높은 순으로 다시 보여줘.
```

For every turn, verify that the LLM changes only the intended mutable Application State and that all rates, interest, statuses, and rankings continue to come from backend DTOs.
