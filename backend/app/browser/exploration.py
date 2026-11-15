EXPLORATION_POLICY = '''Use reversible exploration to reach the information the user requested on any site.
Keep a short working plan: the goal, the current form stage, and the next prerequisite.
When a visible booking, quote, filter or configuration flow is the route to the answer, advance
that flow before searching other pages. Calendars often appear only after choosing a service,
location, staff member, category or duration and pressing Continue. Do not wait for a later
stage's dates or times while the current stage still requires a selection.
If an embedded form shows only a spinner or empty frame, inspect fresh once before leaving it;
it may still be loading. Verify the provider and requested staff/product within the flow when
possible. A target missing from an earlier stage may appear after its prerequisites; do not
leave a working form to search profiles just because the target selector is not visible yet.
Keep search results and selected filters while comparing options. Inspect the current results
instead of reopening the homepage or repeating the search. Never invent sorting URL parameters.
If a sort menu lacks the desired sort, compare the displayed values directly; reopening that menu
will not add a new sorting option. Compare complete result cards, keeping the name, price, seller,
availability and delivery restrictions together. A cheap closed or undeliverable result does not
satisfy a request for delivery now. Use inspect(query=...) and the provided continuation offsets;
retain the best eligible candidate as you read subsequent excerpts, then act on it.
Honor explicit user requirements first, then known user preferences. For an unspecified
reversible choice, use the site's current/default choice or a plausible basic option relevant
to the request. Try it to reveal the next stage instead of asking immediately. Do not infer
personal attributes from a name. Record any provisional choice with the action's choice
argument, for example {"field":"service","value":"Standard haircut (30 minutes)","source":"assumed"}.
When the user specifies or corrects that choice, use source="user" to replace the assumption.
Reuse the recorded field name when replacing a choice; describe the choice in the user's language.
Check the resulting stage after each action. If the choice does not lead toward the goal,
go back and try one other plausible route. Do not keep reopening the entry page or searching
for a separate calendar when the visible form can lead there. After two unproductive routes,
ask for the specific missing choice. Ask sooner if no plausible option exists, private facts
are required, or the next step makes a commitment.
Provisional choices are assumptions for this check, not the user's preferences or permission
to book, reserve, pay or send. Report availability and prices for the selected option only;
do not generalize an empty calendar to every service or duration. Preserve the selected
configuration on follow-ups unless the user changes it. For information-only tasks, stop once
the requested information is visible and verified; do not select a time slot or create a
reservation just to inspect availability.
Once the correct configuration and date are verified in a fresh observation, report the result.
An empty date cell with no descendant slot controls means no slots are shown for that checked
configuration. Do not inspect unrelated dates or keep re-reading the same calendar for reassurance.'''
