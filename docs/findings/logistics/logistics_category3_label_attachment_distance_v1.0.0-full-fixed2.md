# Logistics — Category 3: A Correct Value Reads as Missing in a Long, Multi-Section Answer (1 question)

One answer covers three different breakdowns (order status, carrier, and
shipment month) in a single long response. Every number in all three
breakdowns is independently confirmed correct — but this evaluation's
check for whether a label and its value belong together, which looks at
how far apart they appear in the text, doesn't hold up well once a
response is long and reuses the same words across multiple sections.

## Example

**Question:** "For shipments carried by rail carriers, how does shipment
count break down by order status?"
**Expected:** `Delivered | 15019; Shipped | 6794; Returned | 3491; Pending
| 3084; Cancelled | 2023`
**Answer:** "...15,019 are marked Delivered, 6,794 are Shipped, 3,491 are
Returned, 3,084 are Pending, and 2,023 are Cancelled..." — all five
values stated correctly, right next to the right labels, in the first
paragraph.

Despite that, each label gets flagged as too far from its value, or as
belonging to a different occurrence of the same word elsewhere in the
2,400+ character response (the carrier and month sections reuse order-
status terms and numbers of their own).

## The problem

A proximity-based check for "does this label own this value" starts
producing false negatives once an answer is long enough and reuses the
same vocabulary in more than one place — the correct pairing is there,
early and close together, but a same-named occurrence further down the
response gets matched instead. This is the same category of ambiguity
seen with repeated/overlapping label words in other domains' long
responses, and isn't resolved by a narrow, one-off adjustment without
risking turning other currently-correct matches into false ones.
