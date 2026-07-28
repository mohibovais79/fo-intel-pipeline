# 3 Fully-Worked Example Records

## Example 1: Gary And Mary West Charitable Trust

**Record ID:** `990pf-CA-873374164-853a6434`

**Discovery source:** 990pf

**FO type:** single_family_office

**Confidence:** 0.9


### Qualification evidence

Cross-channel surname match: 'West' appears in both 990-PF (IRS private foundation filing, family surname in name) and SEC 13F filer name. Two independent sources: IRS (family foundation) + SEC (investment management entity). Foundation assets: $333.4M.


### Field provenance

| Field | Value | Status | Source | Method |

|---|---|---|---|---|

| entity_name | Gary And Mary West Charitable Trust | verified | 990pf discovery | filing COVERPAGE/entity name |

| aum_usd | $333.4M | verified | IRS Form 990-PF, Part I (revenue/assets summary) | ProPublica /organizations/{ein}.json financial summary field |

| city | La Jolla | verified | 990pf filing | filing address field |

| state_region | CA | verified | 990pf filing | filing address field |

| principal_name | Gary West | verified | IRS 990-PF XML Part VII | officer name extraction, highest-ranking title |

| principal_title | Trustee | verified | IRS 990-PF XML Part VII | officer title field |

| principal_email | — | could_not_verify | — | Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn |

| principal_phone | — | could_not_verify | — | Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn |

| website | — | could_not_verify | — | not in public filings; requires web search |


### Chain of evidence

1. **Discovery:** Cross-channel surname match: 'West' appears in both 990-PF (IRS private foundation filing, family surname in name) and SEC 13F filer name. Two independent sources: IRS (family foundation) + SEC (investment management entity). Foundation assets: $333.4M.

2. **Qualification:** Cross-channel surname match: 'West' appears in both 990-PF (IRS private foundation filing, family surname in name) and SEC 13F filer name. Two independent sources: IRS (family foundation) + SEC (investment management entity). Foundation assets: $333.4M.

3. **Field verification:** AUM verified from filing data; address verified from filing; principal extracted from officer list; contact fields honestly blank (could_not_verify)


---

## Example 2: Tarbox Family Office, Inc.

**Record ID:** `sec-ca-000106079-Tarbox-Family-Office--Inc--1f5e15db`

**Discovery source:** sec_edgar

**FO type:** single_family_office

**Confidence:** 0.75


### Qualification evidence

IAPD verified (score=3): family_in_other_names: ['TARBOX FAMILY OFFICE, INC.'], relying_advisor_count: 0, family_in_firm_name: TARBOX FAMILY OFFICE, INC.. 13F-HR: 107 holdings, $601.6M AUM. Concentrated portfolio + IAPD metadata = two signals.


### Field provenance

| Field | Value | Status | Source | Method |

|---|---|---|---|---|

| entity_name | Tarbox Family Office, Inc. | verified | sec_edgar discovery | filing COVERPAGE/entity name |

| aum_usd | $601.6M | verified | SEC Form 13F-HR, INFOTABLE VALUE column sum | Aggregated across all 13F-HR filings for firm; VALUE in dollars (post-Jan 2023) |

| city | NEWPORT BEACH | verified | sec_edgar filing | filing address field |

| state_region | CA | verified | sec_edgar filing | filing address field |

| principal_email | — | could_not_verify | — | Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn |

| principal_phone | — | could_not_verify | — | Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn |

| website | — | could_not_verify | — | not in public filings; requires web search |


### Chain of evidence

1. **Discovery:** IAPD verified (score=3): family_in_other_names: ['TARBOX FAMILY OFFICE, INC.'], relying_advisor_count: 0, family_in_firm_name: TARBOX FAMILY OFFICE, INC.. 13F-HR: 107 holdings, $601.6M AUM. Concentrated portfolio + IAPD metadata = two signals.

2. **Qualification:** IAPD verified (score=3): family_in_other_names: ['TARBOX FAMILY OFFICE, INC.'], relying_advisor_count: 0, family_in_firm_name: TARBOX FAMILY OFFICE, INC.. 13F-HR: 107 holdings, $601.6M AUM. Concentrated portfolio + IAPD metadata = two signals.

3. **Field verification:** AUM verified from filing data; address verified from filing; principal extracted from officer list; contact fields honestly blank (could_not_verify)


---

## Example 3: Conrad N Hilton Foundation

**Record ID:** `990pf-CA-943100217-86bcac82`

**Discovery source:** 990pf

**FO type:** single_family_office

**Confidence:** 0.65


### Qualification evidence

990-PF-only Tier 2: shared surname 'MCAULIFFE'. Investment/treasury officer present. Distinctive surname (appears in 3 foundations). Foundation assets: $7099.7M. Single source (IRS only), lower confidence tier.


### Field provenance

| Field | Value | Status | Source | Method |

|---|---|---|---|---|

| entity_name | Conrad N Hilton Foundation | verified | 990pf discovery | filing COVERPAGE/entity name |

| aum_usd | $7099.7M | verified | IRS Form 990-PF, Part I (revenue/assets summary) | ProPublica /organizations/{ein}.json financial summary field |

| city | Westlake Vlg | verified | 990pf filing | filing address field |

| state_region | CA | verified | 990pf filing | filing address field |

| principal_name | Hawley Mcauliffe | verified | IRS 990-PF XML Part VII | officer name extraction, highest-ranking title |

| principal_title | Chairman | verified | IRS 990-PF XML Part VII | officer title field |

| principal_email | — | could_not_verify | — | Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn |

| principal_phone | — | could_not_verify | — | Not available in IRS 990-PF or SEC 13F filings; requires website scrape or LinkedIn |

| website | — | could_not_verify | — | not in public filings; requires web search |


### Chain of evidence

1. **Discovery:** 990-PF-only Tier 2: shared surname 'MCAULIFFE'. Investment/treasury officer present. Distinctive surname (appears in 3 foundations). Foundation assets: $7099.7M. Single source (IRS only), lower confidence tier.

2. **Qualification:** 990-PF-only Tier 2: shared surname 'MCAULIFFE'. Investment/treasury officer present. Distinctive surname (appears in 3 foundations). Foundation assets: $7099.7M. Single source (IRS only), lower confidence tier.

3. **Field verification:** AUM verified from filing data; address verified from filing; principal extracted from officer list; contact fields honestly blank (could_not_verify)


---
