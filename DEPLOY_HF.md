# Deploying MAPLE to Hugging Face Spaces (free, public)

MAPLE runs as a Docker Space. Your Blablador key lives in the Space **Secrets**
(encrypted, not in the repo, not visible to users). `USE_ENV_TOKEN_ONLY=true`
means visitors never see it and never need their own token.

## 1. Create the Space

1. Sign in at https://huggingface.co
2. **New → Space**
3. Owner: you · Space name: `maple` (your URL becomes `https://huggingface.co/spaces/<you>/maple`)
4. **SDK: Docker** · Template: **Blank**
5. Visibility: **Public**
6. Create.

## 2. Push the code

This folder isn't a git repo yet. From inside `celltype_annotator/`:

```bash
git init
git add .
git commit -m "MAPLE initial deploy"
git remote add space https://huggingface.co/spaces/<you>/maple
git push space main    # (or 'master' — match your branch; use --force the first time if HF made an initial commit)
```

`.env` is git-ignored, so your keys are **not** pushed. Only config + code go up.
(Authentication: when prompted, use your HF username and a **write** access token
from https://huggingface.co/settings/tokens as the password.)

## 3. Set the Secrets (Space → Settings → Variables and secrets)

Add these. Mark the API key as a **Secret**; the rest can be plain **Variables**.

| Name | Value | Kind |
|---|---|---|
| `OPENAI_API_KEY` | *your Blablador key* | **Secret** |
| `OPENAI_BASE_URL` | `https://api.blablador.fz-juelich.de/v1` | Variable |
| `OPENAI_MODEL` | `alias-fast` | Variable |
| `LLM_PROVIDER` | `openai` | Variable |
| `USE_ENV_TOKEN_ONLY` | `true` | Variable |
| `NCBI_EMAIL` | `ramadatta.88@gmail.com` | Variable |
| `MAPLE_OPENALEX_MAILTO` | `ramadatta.88@gmail.com` | Variable |

Do **not** add `MAPLE_ENABLE_SCHOLAR` — it defaults to off, which is what we want
for a public URL (Scholar is quota-limited and unnecessary; OpenAlex + PMC +
bioRxiv are free and keyless).

## 4. Go live

The Space rebuilds automatically on push / secret change (watch the **Logs** tab).
When it says *Running*, open the Space URL. Every `git push` redeploys.

## Notes

- **Cost/abuse:** public + shared key means anyone with the link can use your
  Blablador quota. Blablador is Helmholtz academic infra, but if you see heavy
  use, switch the Space to **Private** (Settings → change visibility) or set
  `USE_ENV_TOKEN_ONLY=false` so users must paste their own token.
- **Sleep:** free Spaces sleep when idle and wake on the next visit (first load
  is slower). The disk cache resets on rebuild — MAPLE just re-fetches.
- **Rotate the Smithery key** that appeared earlier in logs; it isn't used here.
