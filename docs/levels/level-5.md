# Level 5: analysis products

The road map stops at level 4. A level 5 sub-recipe turns the clustering into
the products a particular study reports. The HB1603 dive analysis has one;
HB2407 stops at level 4.

## HB1603 dive SVCodes

For each sperm whale dive, every ping is coded by the cluster that dominates
the water between the dive's 99% confidence bounds, giving an SVCode per ping.
A second step tabulates the percent of each dive's duration spent in each
200 m depth interval, per code, as in the NEFSC reference report.

Both steps are [custom steps](../concepts/recipes.md#custom-steps): they call
functions in the AA-SI utilities package directly rather than built-in ops.

--8<-- "data/partials/hb1603-level-5.md"

Part of the [HB1603 dive analysis](hb1603-dive-analysis.md).
