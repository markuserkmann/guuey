from contextlib import suppress

from playwright.async_api import Error


CONSENT_CONTROL = r'''skipped => {
    const visible = element => !!(element.getClientRects().length &&
        getComputedStyle(element).visibility !== 'hidden');
    const containers = document.querySelectorAll(
        '#onetrust-banner-sdk, #CybotCookiebotDialog, #didomi-notice, ' +
        '[role="dialog"], [role="alertdialog"], [aria-modal="true"], ' +
        '[id*="cookie" i], [class*="cookie" i], [data-testid*="cookie" i]'
    );
    const close = /^(close|dismiss|close dialog|close banner|sulje|stäng)( cookie(s)?( dialog| banner)?)?$/i;
    const accept = /^(accept( all)?( cookies)?|allow all( cookies)?|agree( to all)?|i agree|hyväksy( kaikki)?( evästeet)?|salli kaikki( evästeet)?|godkänn( alla)?( kakor)?|acceptera( alla)?( cookies| kakor)?)$/i;
    for (const pattern of [close, accept]) {
        for (const container of containers) {
            if (!visible(container)) continue;
            const text = container.innerText || '';
            if (text.length > 12000 || !/\b(cookies?|eväste\w*|kakor)\b/i.test(text)) continue;
            if (container.querySelector('input[type="password"], input[type="email"], input[autocomplete*="cc-"]')) continue;
            for (const button of container.querySelectorAll('button, [role="button"], input[type="button"]')) {
                const name = (button.getAttribute('aria-label') || button.innerText || button.value || '').trim();
                if (!skipped.includes(name) && visible(button) && !button.disabled && button.getAttribute('aria-disabled') !== 'true' && pattern.test(name)) {
                    return button;
                }
            }
        }
    }
    return null;
}'''


async def dismiss_cookie_dialog(page, active, check_scope, attempts):
    for frame in page.frames[:10]:
        handle = None
        try:
            check_scope(frame.url)
            skipped = [name for url, name in attempts if url == frame.url]
            handle = await frame.evaluate_handle(CONSENT_CONTROL, skipped)
            button = handle.as_element()
            if button is None:
                continue
            await active()
            check_scope(frame.url)
            name = await button.evaluate("element => (element.getAttribute('aria-label') || element.innerText || element.value || '').trim()")
            attempts.add((frame.url, name))
            await button.click(timeout=1500)
            return True
        except (Error, ValueError):
            continue
        finally:
            if handle is not None:
                with suppress(Error):
                    await handle.dispose()
    return False
