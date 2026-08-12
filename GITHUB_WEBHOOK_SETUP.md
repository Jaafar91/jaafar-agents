# GitHub-to-Telegram status updates

This service can forward Android pull-request and build results from GitHub to the Telegram administrator chat.

## 1. Set the Render environment variable

Generate a long random value and add it in the Render service environment:

```
GITHUB_WEBHOOK_SECRET=replace-with-a-long-random-secret
```

Do not put this value in GitHub or in the repository.

## 2. Configure the GitHub webhook

In [Jaafar91/jaafar-open-ai settings](https://github.com/Jaafar91/jaafar-open-ai/settings/hooks), add a webhook:

- **Payload URL:** `https://jaafar-agents.onrender.com/github/webhook`
- **Content type:** `application/json`
- **Secret:** exactly the value of `GITHUB_WEBHOOK_SECRET` in Render
- **SSL verification:** enabled
- **Events:** select individual events, then choose **Pull requests** and **Workflow runs**
- **Active:** enabled

GitHub signs each event. Render rejects any request without a valid signature.

## Telegram messages

After configuration, the authorized Telegram chat receives:

- a link when a pull request is opened or ready for review;
- a notification when it is merged or closed;
- a success or failure message when the Android GitHub Actions workflow completes;
- the Actions-run link where the APK can be downloaded from **Artifacts**.

The existing `/feature <request>` command continues to create the GitHub issue and reply with its link.