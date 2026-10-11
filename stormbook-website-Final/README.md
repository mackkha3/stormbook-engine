# Stormbook Website — Popup Form Only

This version preserves the previous yellow Stormbook website design.

Only one behaviour changed:

- The form is no longer visible anywhere on the page.
- The form opens only after a visitor clicks a “Book the audit” call-to-action.
- The public booking calendar remains removed.

## Form delivery

Add your form endpoint to this line in `index.html`:

```html
<meta name="stormbook-form-endpoint" content="" />
```

Without an endpoint, the page falls back to opening an email to `mack@stormbook.ai` with the submitted details.


## Latest update
- Added a service tiers section (Pilot, Standard, Premium) without showing prices.
- Added a dedicated AI voice highlight area in the How It Works section.
- Premium now clearly shows Voice AI as an upgrade path.
- All Book the audit buttons still open the popup form only.


## Latest change
- Changed the homeowner-experience button label to “See it in action”.
- Added a required phone field to the popup audit form.

## Latest requested fixes
- Replaced the previous Storm Eye logo with the supplied Stormbook S logo in the header, footer, and branded UI marks.
- Changed the top navigation order to: The leak, How it works, The math, Services, FAQ.
- Fixed the Standard, Premium, and Founding Cohort audit links so they reliably open the existing GHL form modal.


## Mobile GHL modal fix
- GHL iframe now starts loading only after the custom modal is visible.
- Removed lazy loading from the hidden iframe.
- Added a fixed 1192px form height and resize events for the GHL embed script.
- Added a temporary loading indicator until the iframe finishes loading.
- No other page content, layout, branding, navigation, or CTA behaviour was changed.


## Stability fix
- Removed all automatic iframe focus so iPhone no longer opens the keyboard before the modal appears.
- Preloads the GHL iframe directly and removes the external resize script.
- Makes every audit trigger use one capture-phase modal handler, so links work repeatedly.
- Prevents 3-D/decorative layers from intercepting Standard, Premium, founding-cohort, or bottom CTA clicks.
- Fully disables the hidden modal's pointer events and restores page scrolling cleanly after close.
