# Using Guuey

[Documentation index](README.md)

## Hand off a task

Describe the result you want and the constraints that affect it. Useful details include budget and currency, location, delivery address or area, dates, time window, quantities, dietary requirements, and preferences. Include a website URL if you want the agent to work on a specific site.

The worker chooses chat, research, browser interaction, or a clarification. Requests to order, buy, book, submit, or change a website account route to the browser even when comparison comes first. Requests for an explanation, an unsent draft, or a sourced comparison can stay in chat or research. A request to check availability does not authorize a booking.

When the agent needs information, use the request card in the conversation. Private passwords, one-time codes, and sign-in links belong in the private-input card. Card entry uses manual takeover. The [browser guide](browser.md) explains these controls.

## Common workflows

| Workflow | What to provide | What to review |
|---|---|---|
| Food ordering | Delivery location, dietary needs, budget, quantities, preferred site if any | Basket contents, substitutions, delivery fees, total, and final order approval |
| Product comparison | Budget, exact requirements, preferred condition, delivery country | Configuration, seller, stock evidence, warranty, delivery, and unknown checks |
| Appointment checks | Provider or staff member, service, date, time range | The service and duration checked, available slots, and any provisional choices |
| Travel comparison | Destination, dates, guests, budget, preferences | Date-specific availability, rate conditions, fees, and booking approval |
| Recurring updates | Research instructions, match condition, interval, timezone | Check history, supporting sources, and Telegram opt-in |

These workflows depend on the target site's access and controls. The agent may ask for a missing private fact or manual interaction when it cannot finish a step.

## Research modes

| Mode | Maximum research rounds | Research deadline |
|---|---|---|
| Fast | 1 | 45 seconds |
| Auto | 2 | 120 seconds |
| Deep | 3 | 240 seconds |

These are execution budgets, not promised response times. Browser tasks use separate limits. Research may finish with a partial answer when evidence remains unavailable. Eligible partial research runs can continue in Deep; browser and recurring runs use their own follow-up or Check now flow.

Store, price and pickup lookups use normal web search first. If checking websites in a browser could help, Guuey keeps the search answer and asks whether to continue, noting that it will take longer. Choose **Yes, use browser** to continue the original request with its sources, or **Keep web results** to dismiss the offer without opening a browser. You can also reply yes or no in the chat.

The composer's **Force browser** option is a debugging override and starts unchecked. Explicit browser requests and website actions such as adding items to a cart can start directly. An explicit request not to use a browser takes precedence.

## Read results and progress

The conversation displays submitted messages immediately, shows delivery feedback, and streams run progress. Research and browser runs can begin with a brief description of the intended work. That message is a progress update; evidence and results arrive separately.

Research answers include source links and checked comparisons. A value labelled **Reported** comes from evidence that does not establish current freshness. Missing hard requirements appear under **To confirm**. Check those labels before relying on a price, stock count, delivery claim, or appointment slot.

Open **Debug** to inspect routing, model calls, retrieval activity, timing, and browser steps. The panel sits beside the conversation on desktop and below it on smaller screens. Diagnostics can contain task and website content; treat exports as private.

## Continue a conversation

Follow-ups preserve the conversation context and can refine existing requirements: change the budget, exclude a candidate, adjust dates, or request a different comparison. Keep related follow-ups in the same chat.

After a browser reply, the worker keeps the page open for 60 seconds by default. A browser follow-up within that window can reuse the basket, form state, and selected tab. The operator can configure a window from 1 to 300 seconds. New tasks still receive fresh limits and approval requests. After expiry or a worker restart, a saved login may restore authentication, but the same live page is no longer guaranteed.

## Conversations and attachments

Search saved conversations by title or message text and pin frequently used chats. Named users have separate histories. Recurring-task conversations appear in their own sidebar section with activity indicators and unread updates.

Attach files using the attachment button or drag-and-drop:

- Up to three files per message, with distinct filenames.
- UTF-8 text only, up to 50 KiB per file and 100 KiB combined.
- Supported extensions: `.txt`, `.md`, `.csv`, `.json`, `.log`, `.yaml`, `.yml`, `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.html`, `.css`, `.xml`, `.sql`, `.toml`, and `.ini`.
- Images, PDFs, office documents, empty files, and binary content are unsupported.

Attached content is sent with the message to the model and stored with the conversation for follow-up questions. Expand an attachment in the conversation to inspect it. The backend marks attachment content as untrusted reference data.

## Speak a request

Select the microphone beside Send, dictate, then select Stop or press Escape. Review or edit the transcription and send it when ready. Dictation fills the draft; speaking does not submit the task automatically. See [voice input](voice-input.md) for permissions, languages, and data flow.

## Schedule matching updates

Open **Recurring tasks**, create a task, and specify what to research and when an update should be sent. The worker runs checks even with the browser closed. Matching updates appear in Guuey; Telegram delivery also requires a linked account and **Send matches to Telegram** on that task.

See [recurring tasks](recurring-tasks.md) for schedule behavior and [Telegram](telegram.md) for linking and delivery status.
