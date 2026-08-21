# Quick Quote Telegram control

Jaafar Agents can control both apps from the same authorized Telegram chat.

## Quick Quote commands

- `/quote Add a customer address field to new quotations`
  - Creates an OpenAI feature-request issue in `Jaafar91/quotation-maker`.

- `/quote copilot Add branded PDF export with the company logo`
  - Creates a Copilot feature-request issue in `Jaafar91/quotation-maker`.

- `/quote merge 12`
  - Squash-merges Quick Quote pull request #12.

The existing `/openai`, `/copilot`, and `/merge` commands still target Font Creator.

## Render configuration

No new token is required if the existing `GITHUB_TOKEN` can access both repositories.

Optional explicit configuration:

```
QUOTATION_APP_REPOSITORY=Jaafar91/quotation-maker
```

## GitHub status messages

Add the existing webhook to **Quick Quote** too:

- Repository: `Jaafar91/quotation-maker` → **Settings** → **Webhooks**
- Payload URL: `https://jaafar-agents.onrender.com/github/webhook`
- Content type: `application/json`
- Secret: the existing `GITHUB_WEBHOOK_SECRET` from Render
- SSL verification: enabled
- Events: **Pull requests** and **Workflow runs**

After that, the Telegram chat receives Quick Quote pull-request and build results alongside Font Creator updates.
