# Font Creator iOS Telegram control

Jaafar Agents can control Font Creator Android, Quick Quote, and Font Creator iOS from one authorized Telegram chat.

## iOS commands

- `/ios Add a new signature style`
  - Creates an OpenAI feature-request issue in `Jaafar91/jaafar-fonts-ios`.

- `/ios copilot Improve the PDF annotation workflow`
  - Creates a Copilot feature-request issue in the iOS repository.

- `/ios merge 12`
  - Squash-merges Font Creator iOS pull request #12.

The existing commands remain separate:

- `/openai`, `/copilot`, and `/merge` — Font Creator Android
- `/quote ...` — Quick Quote

## Render configuration

No additional token is needed if the existing GitHub token can access the iOS repository.

Optional explicit setting:

```
IOS_FONT_CREATOR_REPOSITORY=Jaafar91/jaafar-fonts-ios
```

## GitHub-to-Telegram updates

Add the existing webhook to `Jaafar91/jaafar-fonts-ios`:

- Payload URL: `https://jaafar-agents.onrender.com/github/webhook`
- Content type: `application/json`
- Secret: your existing `GITHUB_WEBHOOK_SECRET`
- SSL verification: enabled
- Events: **Pull requests** and **Workflow runs**
