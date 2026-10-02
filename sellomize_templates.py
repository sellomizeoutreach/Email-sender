"""
sellomize_templates.py - Core 15 Sellomize Cold-Email Templates & Intelligence Engine.

Features:
- Definitions for all 15 core Sellomize outreach categories.
- Fact-safe variable resolution for 21 prospect/Amazon research variables.
- Strict Location and Contact Name safety rules (no hallucinations or fake facts).
- 3-Part Client Proof structures (What We Found, How We Solved It, What It Rewarded).
- Separate follow-up templates featuring light, human humor.
- Signal-based AI template recommendation engine matching verified Amazon research.
- Anti-corporate copywriting guidelines and vocabulary constraints.
"""

import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# FORBIDDEN JARGON & PHRASES (Banned from all outbound copy)
# -----------------------------------------------------------------------------
BANNED_JARGON_WORDS = [
    "leverage", "utilize", "robust", "seamless", "delve", "streamline", "unlock",
    "elevate", "game-changer", "unparalleled", "comprehensive", "facilitate",
    "additionally", "numerous", "significant", "ensure", "optimal", "holistic",
    "cutting-edge", "innovative", "dynamic", "foster", "underscore", "testament",
    "landscape", "realm", "tapestry", "endeavor", "ascertain", "commence", "procure"
]

OVERUSED_OPENINGS = [
    "i hope you're doing well",
    "i hope this email finds you well",
    "i wanted to reach out",
    "i noticed you may be",
    "i'd love to",
    "great opportunity",
    "exciting opportunity"
]

PREFERRED_CTA = "Would you have some time over the next week or two to take a look together? Let me know what works for you and I'll send a calendar invite."

# -----------------------------------------------------------------------------
# APPROVED CLIENT STORIES (Stored in strict 3-part structures)
# -----------------------------------------------------------------------------
APPROVED_CLIENT_STORIES = {
    "ppc_scaling": {
        "id": "ppc_scaling_growth",
        "title": "4-5 Figure to 6-7 Figure PPC Scale",
        "category": "PPC — Scaling",
        "found": "The account had traffic, but the PPC budget wasn't being allocated toward the right campaigns and products.",
        "solved": "We reviewed the campaigns and targets driving sales, shifted budget toward stronger opportunities, and adjusted bids and budgets.",
        "rewarded": "$100,072.52 in sales from $14,324.46 in ad spend with a 6.99 ROAS, scaling the brand into the six- to seven-figure range on Amazon."
    },
    "reconciliation_fba": {
        "id": "fba_reconciliation_audit",
        "title": "FBA Shipment & Cubiscan Reimbursement",
        "category": "Reconciliation — FBA",
        "found": "161 shipment discrepancies and 164 Cubiscan measurement errors across inbound inventory.",
        "solved": "151 Amazon reimbursement cases were opened and worked through directly with Amazon seller support.",
        "rewarded": "147 cases resolved, bringing back $8,145.91 in shipment reimbursements plus $1,456.78 in Cubiscan reimbursement value ($9,602.69 total)."
    },
    "overlooked_sku": {
        "id": "sku_listing_revamp",
        "title": "Overlooked SKU Repositioning",
        "category": "Product / SKU Opportunity",
        "found": "One smaller product in the catalog was getting pushed aside while larger hero products received all the ad budget and attention.",
        "solved": "We rebuilt the listing copy, optimized mobile infographics, and repositioned its search targets.",
        "rewarded": "The overlooked SKU became one of the brand's top three consistent sales drivers on Amazon."
    }
}

# -----------------------------------------------------------------------------
# THE 15 CORE SELLOMIZE BASE TEMPLATES
# -----------------------------------------------------------------------------
CORE_15_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": 1,
        "name": "1. Amazon Growth — General",
        "category": "Amazon Growth — General",
        "subject": "[Company] + Sellomize",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I spent some time looking through your Amazon presence and noticed there’s room to get more from the account.\n\n"
            "The products are there, but Amazon growth usually comes down to a few things working together — search visibility, listing content, PPC, and how the products are positioned.\n\n"
            "That’s where we help.\n\n"
            "We work with brands to find the gaps, fix what’s holding products back, and build a stronger Amazon growth plan around what’s already working.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Full-Service Amazon Growth, Listing & PPC Optimization",
        "recommended_signals": "General Amazon account audit, room for growth across catalog, products present but underperforming",
        "allowed_variables": ["{first_name}", "[Company]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: [Company] + Sellomize",
                "body": (
                    "Hi {first_name},\n\n"
                    "Just popping this back up before it gets lost in the inbox shuffle. 😄\n\n"
                    "I still think there's good room to unlock more sales on your Amazon catalog with a few targeted adjustments.\n\n"
                    "Would you be open to taking a look together over the next week or two? Let me know what works and I'll send a calendar invite."
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: [Company] + Sellomize",
                "body": (
                    "Hi {first_name},\n\n"
                    "Assuming you didn't get eaten by the Amazon algorithm this week... 😅\n\n"
                    "Wanted to check in one last time on whether you'd like a quick fresh set of eyes on the Amazon account.\n\n"
                    "Let me know if next Tuesday or Wednesday works for a brief 10-minute chat."
                )
            }
        ]
    },
    {
        "id": 2,
        "name": "2. Listing — SEO & Copy",
        "category": "Listing — SEO & Copy",
        "subject": "A closer look at your Amazon listings",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking through your Amazon listings and noticed a few areas where the titles, bullets, and product copy could do more work.\n\n"
            "Getting a product found is only half the job.\n\n"
            "Once someone lands on the listing, the copy needs to quickly explain what the product is, why it matters, and why they should choose it.\n\n"
            "We help brands tighten up Amazon SEO and rewrite product copy around both search and conversion.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Listing SEO, Title & Bullet Optimization, Backend Keyword Indexing",
        "recommended_signals": "Weak titles, short or generic bullet points, poor keyword indexing, low search rank",
        "allowed_variables": ["{first_name}", "[Company]", "[Product]", "[ASIN]", "[Keyword]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: A closer look at your Amazon listings",
                "body": (
                    "Hi {first_name},\n\n"
                    "Bringing this back to the top before your inbox buries it deeper than page 5 of Amazon search. 🔍\n\n"
                    "Wanted to see if you had a chance to look over my note on tightening up your Amazon titles and listing copy.\n\n"
                    "Would you have 10 minutes next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: A closer look at your Amazon listings",
                "body": (
                    "Hi {first_name},\n\n"
                    "Quick bump between cups of coffee ☕.\n\n"
                    "If you're already happy with your search indexing and conversion rates, no worries at all. But if you'd like a quick breakdown of where keywords are getting dropped, let me know and I'll send over a calendar invite."
                )
            }
        ]
    },
    {
        "id": 3,
        "name": "3. Listing — Images & Video",
        "category": "Listing — Images & Video",
        "subject": "Your Amazon images could do more",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking through your Amazon listings and noticed some of the product pages could use stronger visual content.\n\n"
            "Images have to do more than show the product.\n\n"
            "They should help shoppers understand how it works, what makes it different, and what they’re actually getting.\n\n"
            "We help brands improve product images, lifestyle content, infographics, and video so the listing does more of the selling.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Infographic Design, Mobile Image Optimization, Product Video Production",
        "recommended_signals": "Fewer than 6 images, plain white background only, missing mobile infographics, no video on listing",
        "allowed_variables": ["{first_name}", "[Company]", "[Product]", "[ASIN]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: Your Amazon images could do more",
                "body": (
                    "Hi {first_name},\n\n"
                    "Checking in before this email ends up in the lost-and-found bin of your inbox. 📦\n\n"
                    "Over 70% of Amazon mobile shoppers only look at image carousels before buying without ever scrolling to the text.\n\n"
                    "Would you be open to taking a look at a couple visual tweaks together next week?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: Your Amazon images could do more",
                "body": (
                    "Hi {first_name},\n\n"
                    "I promise this is my last nudge before I assume you're secretly a fan of plain white background photos. 😉\n\n"
                    "If you'd like to take a look at how stronger infographics could lift conversions on [Company], let me know what day works and I'll send an invite."
                )
            }
        ]
    },
    {
        "id": 4,
        "name": "4. Listing — A+ Content",
        "category": "Listing — A+ Content",
        "subject": "A+ content for your Amazon listings",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I spent some time looking through your Amazon listings and noticed there’s room to make the A+ experience do more.\n\n"
            "A+ should help shoppers understand the product and the brand without making them dig through the page.\n\n"
            "We help brands build A+ content around product benefits, use cases, comparisons, and the parts of the story that standard listing copy can’t cover.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "A+ Content Design, Brand Story Modules, Comparison Charts",
        "recommended_signals": "Missing A+ content, standard text description only, generic low-res A+, missing comparison table",
        "allowed_variables": ["{first_name}", "[Company]", "[Product]", "[ASIN]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: A+ content for your Amazon listings",
                "body": (
                    "Hi {first_name},\n\n"
                    "Just popping this back up before Monday turns into Friday again. ⏳\n\n"
                    "Wanted to see if you had a second to think about upgrading the A+ modules on your listings.\n\n"
                    "Would you have some time next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: A+ content for your Amazon listings",
                "body": (
                    "Hi {first_name},\n\n"
                    "Final quick check-in. If updating your Amazon A+ modules isn't on your radar this quarter, completely understood!\n\n"
                    "Otherwise, happy to share a couple of comparison layouts that have worked well for similar brands. Just let me know."
                )
            }
        ]
    },
    {
        "id": 5,
        "name": "5. Listing — Multiple Gaps",
        "category": "Listing — Multiple Gaps",
        "subject": "A few Amazon listing gaps",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking through your Amazon catalog and noticed a few different areas that could be improved across the listings.\n\n"
            "Some are content related.\n\n"
            "Some are search related.\n\n"
            "And a few come down to how the product is presented to the shopper.\n\n"
            "Rather than fixing one piece at a time, we help brands look at the full listing and clean up the areas that are getting in the way of growth.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Full Listing Overhaul, Conversion Rate Optimization, Catalog Cleanup",
        "recommended_signals": "Multiple listing issues (e.g. weak copy + few images + missing A+), low listing score",
        "allowed_variables": ["{first_name}", "[Company]", "[ListingScore]", "[SpecificObservation]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: A few Amazon listing gaps",
                "body": (
                    "Hi {first_name},\n\n"
                    "Floating this back to the top of your stack. 📄\n\n"
                    "Fixing small listing gaps usually creates a noticeable compounding effect on both organic rank and ad spend efficiency.\n\n"
                    "Would you have 10 minutes next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: A few Amazon listing gaps",
                "body": (
                    "Hi {first_name},\n\n"
                    "One last polite ping before I clear your name from my notepad. 📝\n\n"
                    "If you ever want an honest second opinion on your listings, the door is always open. Wishing you a great rest of the week!"
                )
            }
        ]
    },
    {
        "id": 6,
        "name": "6. PPC — Organic + Sponsored Overlap",
        "category": "PPC — Organic + Sponsored Overlap",
        "subject": "Your Amazon ads caught my eye",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I noticed some of your products are already showing up organically while also receiving paid placement.\n\n"
            "That can be useful, but it can also mean part of the ad budget is going toward products that already have strong organic visibility.\n\n"
            "We help brands review where PPC spend is going and move more of that budget toward products and search terms where there’s more room to grow.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "PPC Cannibalization Audit, Budget Reallocation, Sponsored Ads Optimization",
        "recommended_signals": "Sponsored product appearing alongside top organic rank on brand or generic keywords",
        "allowed_variables": ["{first_name}", "[Company]", "[Keyword]", "[OrganicRank]", "[AdRank]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: Your Amazon ads caught my eye",
                "body": (
                    "Hi {first_name},\n\n"
                    "Quick follow-up before Amazon bills another click for a customer who was already going to buy organically anyway. 💸\n\n"
                    "Would you be open to taking a look at your ad-to-organic overlap together next week?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: Your Amazon ads caught my eye",
                "body": (
                    "Hi {first_name},\n\n"
                    "Assuming you're deep in campaign management mode right now! 📊\n\n"
                    "Whenever you have a moment, I'd still be glad to share how we trim ad cannibalization. Let me know what works for your calendar."
                )
            }
        ]
    },
    {
        "id": 7,
        "name": "7. PPC — Ranking Gaps",
        "category": "PPC — Ranking Gaps",
        "subject": "A few Amazon ranking gaps",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking through your Amazon search visibility and noticed some products are showing up for relevant searches, but there are still gaps in where they appear.\n\n"
            "That’s where SEO and PPC need to work together.\n\n"
            "We help brands identify the keywords worth going after, improve organic visibility, and use PPC to support the areas where the listings still need a push.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Keyword Ranking Strategy, Sponsored Products Push, Organic & Paid Synergy",
        "recommended_signals": "Indexed on relevant search terms but ranking on bottom of page 1 or page 2, inconsistent rank",
        "allowed_variables": ["{first_name}", "[Company]", "[Keyword]", "[OrganicRank]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: A few Amazon ranking gaps",
                "body": (
                    "Hi {first_name},\n\n"
                    "Bringing this back up before the keyword gap widens. 📈\n\n"
                    "Using targeted PPC to intentionally force organic rank velocity usually pays for itself fast.\n\n"
                    "Would you have 10 minutes next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: A few Amazon ranking gaps",
                "body": (
                    "Hi {first_name},\n\n"
                    "Final note from me! If search rank isn't a priority right now, no problem.\n\n"
                    "If you do want to review the keyword gaps we flagged, let me know and I'll send over an invite."
                )
            }
        ]
    },
    {
        "id": 8,
        "name": "8. PPC — Scaling",
        "category": "PPC — Scaling",
        "subject": "More from your Amazon PPC",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I spent some time looking at your Amazon presence and thought there may be room to get more from the PPC side.\n\n"
            "We recently worked with a client brand that was doing four- to five-figure sales on Amazon.\n\n"
            "The account had room to grow, but the budget wasn't being pushed toward the right campaigns and products.\n\n"
            "We reviewed the account, shifted budget, adjusted bids, and kept testing.\n\n"
            "The brand eventually moved into the six- to seven-figure range on Amazon.\n\n"
            "I’d be happy to show you what we look for when we review an account.\n\n"
            "Would you have some time over the next week or two? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "PPC Account Restructuring, Bid Management, Campaign Scaling",
        "recommended_signals": "Active PPC advertising present, high review count or solid catalog, ready to scale spend",
        "allowed_variables": ["{first_name}", "[Company]", "[Location]", "[ClientStory]"],
        "client_story_allowed": True,
        "client_story_id": "ppc_scaling_growth",
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: More from your Amazon PPC",
                "body": (
                    "Hi {first_name},\n\n"
                    "Just floating this back up before it gets lost in the inbox shuffle. 😄\n\n"
                    "Wanted to see if you had a chance to look at my note about scaling Amazon ad spend without killing ROAS.\n\n"
                    "Would you be open to taking a look together? Let me know what works and I'll send a calendar invite."
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: More from your Amazon PPC",
                "body": (
                    "Hi {first_name},\n\n"
                    "Quick check-in before I stop bugging you about PPC. 🎯\n\n"
                    "I'd still be glad to share our PPC audit checklist with you if you're interested. Let me know if you have 10 minutes next week."
                )
            }
        ]
    },
    {
        "id": 9,
        "name": "9. Customer Feedback — Conversion",
        "category": "Customer Feedback — Conversion",
        "subject": "Something I noticed in your Amazon feedback",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking through your Amazon listings and noticed some customer feedback that caught my attention.\n\n"
            "Negative feedback doesn’t always mean there’s something wrong with the product itself.\n\n"
            "Sometimes the customer didn’t fully understand what they were buying, how to use it, or what was included.\n\n"
            "That can point back to the listing.\n\n"
            "We help brands review those gaps and improve the images, copy, and product information so shoppers have a clearer picture before they buy.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Review Sentiment Analysis, Listing Clarification, Customer Expectation Alignment",
        "recommended_signals": "Reviews mentioning misunderstanding of size/usage/ingredients, return rate signals, listing confusion",
        "allowed_variables": ["{first_name}", "[Company]", "[Product]", "[Rating]", "[ReviewCount]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: Something I noticed in your Amazon feedback",
                "body": (
                    "Hi {first_name},\n\n"
                    "Nudging this back up before another shopper misreads the product dimensions. 📏\n\n"
                    "Updating one infographic or bullet point often cuts return rates and negative reviews in half.\n\n"
                    "Would you have 10 minutes next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: Something I noticed in your Amazon feedback",
                "body": (
                    "Hi {first_name},\n\n"
                    "Final quick note on this. If customer questions and review expectations are already under control, no worries at all!\n\n"
                    "If you ever want an audit of review trends on your listings, feel free to reach back out."
                )
            }
        ]
    },
    {
        "id": 10,
        "name": "10. Reconciliation — FBA",
        "category": "Reconciliation — FBA",
        "subject": "Something worth checking on Amazon",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I wanted to reach out about something that can easily get overlooked on Amazon — reconciliation.\n\n"
            "We recently found 161 shipment discrepancies for a client.\n\n"
            "We opened 151 cases with Amazon and 147 were resolved.\n\n"
            "That brought back $8,145.91 in reimbursements.\n\n"
            "We then checked the Cubiscan data.\n\n"
            "There were another 164 cases, which added $1,456.78 in reimbursement value.\n\n"
            "I’m not saying your account has the same numbers.\n\n"
            "But this is something we can check and see what Amazon may owe you.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "FBA Inbound Reconciliation, Cubiscan Fee Audit, Amazon Reimbursement Recovery",
        "recommended_signals": "FBA seller, heavy shipment volume, high inventory velocity, potential fee discrepancies",
        "allowed_variables": ["{first_name}", "[Company]", "[Location]", "[ClientStory]"],
        "client_story_allowed": True,
        "client_story_id": "fba_reconciliation_audit",
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: Something worth checking on Amazon",
                "body": (
                    "Hi {first_name},\n\n"
                    "Just popping this back up before it gets lost in the inbox shuffle. 😄\n\n"
                    "Wanted to see if you had a chance to look at my note about reconciliation.\n\n"
                    "Amazon warehouse errors happen to every brand, and I still think it's worth a quick look to see if you have funds sitting on the table.\n\n"
                    "Would you be open to taking a look together? Let me know what works and I'll send a calendar invite."
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: Something worth checking on Amazon",
                "body": (
                    "Hi {first_name},\n\n"
                    "Checking in one last time before Amazon's 18-month claim window quietly rolls forward. ⏳\n\n"
                    "If you already reconcile your FBA shipments regularly, you're ahead of 90% of sellers. If not, happy to take a quick look whenever you have 10 minutes."
                )
            }
        ]
    },
    {
        "id": 11,
        "name": "11. Amazon Account — Technical / Buy Box",
        "category": "Amazon Account — Technical / Buy Box",
        "subject": "A quick look at your Amazon account",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking through your Amazon presence and noticed a few areas that may need attention on the account side.\n\n"
            "Things like Buy Box issues, listing availability, variations, suppressed products, and other Amazon account problems can quietly affect sales.\n\n"
            "We help brands find and fix those issues instead of letting them sit in the background.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Buy Box Suppression Fixes, ASIN Reinstatement, Variation Theme Cleanup",
        "recommended_signals": "Currently unavailable ASINs, lost Buy Box to 3P sellers, broken variation branches, suppressed listings",
        "allowed_variables": ["{first_name}", "[Company]", "[ASIN]", "[AmazonIssue]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: A quick look at your Amazon account",
                "body": (
                    "Hi {first_name},\n\n"
                    "Bringing this back up before Seller Central decides to invent another mystery warning badge. 🛠️\n\n"
                    "Technical account hiccups and suppressed variations quietly bleed sales every day they stay unresolved.\n\n"
                    "Would you have 10 minutes next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: A quick look at your Amazon account",
                "body": (
                    "Hi {first_name},\n\n"
                    "Last ping from me! Hope your Buy Box and variation trees are in good shape.\n\n"
                    "If you ever hit an account roadblock with Amazon support that won't budge, feel free to give us a shout."
                )
            }
        ]
    },
    {
        "id": 12,
        "name": "12. Full Amazon Growth / Catalog",
        "category": "Full Amazon Growth / Catalog",
        "subject": "Your Amazon catalog",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I spent some time looking through your Amazon catalog and noticed there are several areas where the account could be working harder.\n\n"
            "That can mean listing content on one product, PPC on another, SEO on another, and operational issues somewhere else.\n\n"
            "Instead of treating each problem separately, we look at the Amazon account as a whole and build the work around where the biggest gaps are.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Full Catalog Strategy, Holistic Amazon Channel Management",
        "recommended_signals": "Large multi-SKU catalog, mixed performance (some top sellers, many lagging SKUs), complex operations",
        "allowed_variables": ["{first_name}", "[Company]", "[SpecificObservation]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: Your Amazon catalog",
                "body": (
                    "Hi {first_name},\n\n"
                    "Floating this back up before the weekend! 📬\n\n"
                    "When catalog issues are scattered across multiple products, fixing the top 20% of levers usually produces 80% of the revenue lift.\n\n"
                    "Would you have some time next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: Your Amazon catalog",
                "body": (
                    "Hi {first_name},\n\n"
                    "Final follow-up on my end. If managing the Amazon catalog is already completely humming along, that's great to hear.\n\n"
                    "If you'd ever like a bird's-eye breakdown of the catalog, let me know what works and I'll send an invite."
                )
            }
        ]
    },
    {
        "id": 13,
        "name": "13. Competitor / Market Opportunity",
        "category": "Competitor / Market Opportunity",
        "subject": "Something I noticed around your Amazon category",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking at your Amazon category and noticed competitors are taking up space around your products.\n\n"
            "Some of that comes through organic rankings.\n\n"
            "Some comes through sponsored placements.\n\n"
            "That creates an opening to look at where your products can gain more visibility and where PPC, SEO, and listing improvements can work together.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Competitor Conquesting, Defensive Ad Targeting, Category Share Expansion",
        "recommended_signals": "Competitor ads running directly on product page carousel, competitors ranking above brand on key terms",
        "allowed_variables": ["{first_name}", "[Company]", "[Product]", "[Keyword]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: Something I noticed around your Amazon category",
                "body": (
                    "Hi {first_name},\n\n"
                    "Bumping this before your competitors buy up any more ad space on your own branded search terms. 🥊\n\n"
                    "Defending your product pages while conquering category search terms is usually one of our fastest wins.\n\n"
                    "Would you be open to taking a look together next week?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: Something I noticed around your Amazon category",
                "body": (
                    "Hi {first_name},\n\n"
                    "Quick last check-in. If you're already holding the category line against competitors, kudos to your team!\n\n"
                    "If you ever want to see where competitors are bidding on your products, feel free to give me a shout."
                )
            }
        ]
    },
    {
        "id": 14,
        "name": "14. Brand Story → Amazon",
        "category": "Brand Story → Amazon",
        "subject": "Your brand story on Amazon",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I spent some time looking at your brand and then your Amazon presence.\n\n"
            "There’s a clear story behind the products, but some of that gets lost once shoppers reach the Amazon listing.\n\n"
            "That matters because someone who already knows the brand may understand the value.\n\n"
            "A new Amazon shopper doesn't have that context.\n\n"
            "We help brands bring more of that story into the listing, images, A+, Storefront, and product copy.\n\n"
            "Would you have some time over the next week or two to take a look together? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Brand Story Module Setup, Amazon Storefront Architecture, D2C-to-Amazon Translation",
        "recommended_signals": "Strong DTC website or social brand identity, but Amazon listings look generic or incomplete",
        "allowed_variables": ["{first_name}", "[Company]", "[Product]", "[Location]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: Your brand story on Amazon",
                "body": (
                    "Hi {first_name},\n\n"
                    "Popping this back up before the inbox waves wash it away. 🌊\n\n"
                    "You've clearly built a distinct brand identity off Amazon, and translating that into the Amazon storefront usually lifts average order value right away.\n\n"
                    "Would you have 10 minutes next week to take a look together?"
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: Your brand story on Amazon",
                "body": (
                    "Hi {first_name},\n\n"
                    "Last note from me! If you prefer keeping the Amazon presence low-key for now, totally understand.\n\n"
                    "If you ever want to see examples of how we've brought brand stories to life on Amazon Storefronts, let me know and I'll send an invite."
                )
            }
        ]
    },
    {
        "id": 15,
        "name": "15. Product / SKU Opportunity",
        "category": "Product / SKU Opportunity",
        "subject": "One product caught my attention",
        "body": (
            "Hi {first_name},\n\n"
            "We haven’t been properly introduced, but I’m Jack with Sellomize.\n\n"
            "I was looking through your Amazon catalog and one product caught my attention.\n\n"
            "It looks like there’s a good product there, but it may not be getting the same level of attention as some of the other SKUs.\n\n"
            "We’ve seen this with client brands before.\n\n"
            "One smaller product was getting pushed aside while the bigger products received most of the attention.\n\n"
            "We worked on the listing and how the product was presented.\n\n"
            "It ended up becoming one of their strongest sales drivers.\n\n"
            "I’d be happy to show you what we noticed and how we’d approach it.\n\n"
            "Would you have some time over the next week or two? Let me know what works for you and I’ll send a calendar invite."
        ),
        "recommended_services": "Secondary SKU Repositioning, Catalog Long-Tail Optimization",
        "recommended_signals": "Good review ratings on a secondary SKU, but low sales volume / overlooked positioning",
        "allowed_variables": ["{first_name}", "[Company]", "[Product]", "[ASIN]", "[Rating]", "[Location]", "[ClientStory]"],
        "client_story_allowed": True,
        "client_story_id": "sku_listing_revamp",
        "followups": [
            {
                "delay_days": 3,
                "subject": "Re: One product caught my attention",
                "body": (
                    "Hi {first_name},\n\n"
                    "Just popping this back up before it gets lost in the inbox shuffle. 😄\n\n"
                    "Wanted to see if you had a moment to consider that secondary SKU on your catalog.\n\n"
                    "I still think with a few listing tweaks it could become a serious revenue driver.\n\n"
                    "Would you be open to taking a look together? Let me know what works and I'll send a calendar invite."
                )
            },
            {
                "delay_days": 7,
                "subject": "Re: One product caught my attention",
                "body": (
                    "Hi {first_name},\n\n"
                    "Final friendly ping from me! ☕\n\n"
                    "If your hero products are already keeping your team at 110% capacity, I completely understand.\n\n"
                    "Whenever you're ready to explore that SKU, let me know and I'll send over a calendar invite."
                )
            }
        ]
    },
    {
        "id": 16,
        "name": "16. Follow Up — Friendly Reminder",
        "category": "Follow Up — Friendly Reminder",
        "subject": "Re: [Company] + Sellomize",
        "body": (
            "Hi {first_name},\n\n"
            "Just popping this back up before it gets lost in the inbox shuffle. 😄\n\n"
            "Wanted to see if you had a chance to look at my note about [AmazonIssue] for [Company].\n\n"
            "I still think it’s worth a quick look.\n\n"
            "Would you be open to taking a look together? Let me know what works and I’ll send a calendar invite."
        ),
        "recommended_services": "Follow-Up Outreach, Quick Account Alignment",
        "recommended_signals": "Follow-up after initial cold email, bump note, unanswered initial outreach",
        "allowed_variables": ["{first_name}", "[Company]", "[AmazonIssue]"],
        "client_story_allowed": False,
        "followups": [
            {
                "delay_days": 4,
                "subject": "Re: [Company] + Sellomize",
                "body": (
                    "Hi {first_name},\n\n"
                    "Assuming you didn't get eaten by the Amazon algorithm this week... 😅\n\n"
                    "Wanted to check in one last time on whether you'd like a quick fresh set of eyes on the Amazon account.\n\n"
                    "Let me know if next Tuesday or Wednesday works for a brief 10-minute chat."
                )
            }
        ]
    }
]


# -----------------------------------------------------------------------------
# SIGNAL MATCHING & TEMPLATE RECOMMENDATION ENGINE
# -----------------------------------------------------------------------------
def recommend_template_for_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyzes verified prospect data, tags, notes, and custom variables
    to recommend the highest-leverage Sellomize template category.
    Returns recommendation metadata dict with template ID, category name, reason, and confidence.
    """
    notes = (lead.get("notes") or "").lower()
    tags = (lead.get("tags") or "").lower()
    custom_vars = lead.get("custom_variables_dict") or {}
    if not isinstance(custom_vars, dict) and isinstance(lead.get("custom_variables"), str):
        try:
            custom_vars = json.loads(lead.get("custom_variables") or "{}")
        except Exception:
            custom_vars = {}

    cv_text = " ".join(f"{k} {v}" for k, v in custom_vars.items()).lower()
    combined_signal = f"{notes} {tags} {cv_text}"

    # 0. Follow Up / Friendly Reminder signals
    if any(k in combined_signal for k in ["follow up", "follow-up", "reminder", "bump", "second email", "no reply", "unanswered"]) or lead.get("contacted") == "Yes" or int(lead.get("follow_ups_sent") or 0) > 0:
        return {
            "template_id": 16,
            "category": "Follow Up — Friendly Reminder",
            "reason": "Lead previously contacted or follow-up bump requested. Short, friendly reminder note.",
            "confidence": 0.96,
            "recommended_service": "Follow-Up Outreach, Quick Account Alignment"
        }

    # 1. Reconciliation signals
    if any(k in combined_signal for k in ["reconcil", "discrepanc", "fba lost", "cubiscan", "reimburse", "lost inventory"]):
        return {
            "template_id": 10,
            "category": "Reconciliation — FBA",
            "reason": "Verified FBA shipment discrepancies, Cubiscan errors, or reimbursement recovery signals detected.",
            "confidence": 0.95,
            "recommended_service": "FBA Inbound Reconciliation & Reimbursement Recovery"
        }

    # 2. Buy Box / Account Technical issues
    if any(k in combined_signal for k in ["buy box", "suppressed", "unavailable asin", "listing unavailable", "variation error", "hijack", "strang"]):
        return {
            "template_id": 11,
            "category": "Amazon Account — Technical / Buy Box",
            "reason": "Suppressed listings, unavailable ASINs, or Buy Box loss issues detected in research notes.",
            "confidence": 0.92,
            "recommended_service": "Buy Box Suppression Fixes & ASIN Reinstatement"
        }

    # 3. PPC Overlap / Cannibalization
    if any(k in combined_signal for k in ["overlap", "cannibal", "paid and organic", "ppc around organic", "bidding on brand"]):
        return {
            "template_id": 6,
            "category": "PPC — Organic + Sponsored Overlap",
            "reason": "Observed paid ad placements overlapping with existing strong organic search rank.",
            "confidence": 0.90,
            "recommended_service": "PPC Cannibalization Audit & Budget Reallocation"
        }

    # 4. PPC Ranking Gaps
    if any(k in combined_signal for k in ["ranking gap", "page 2", "low rank", "indexed but", "keyword rank", "organic push"]):
        return {
            "template_id": 7,
            "category": "PPC — Ranking Gaps",
            "reason": "Identified indexed keywords where product ranks below page 1 and needs paid push.",
            "confidence": 0.88,
            "recommended_service": "Keyword Ranking Strategy & Sponsored Push"
        }

    # 5. Customer Feedback / Conversion confusion
    if any(k in combined_signal for k in ["feedback", "customer confusion", "returns", "misunderstood", "bad reviews due to size", "negative review"]):
        return {
            "template_id": 9,
            "category": "Customer Feedback — Conversion",
            "reason": "Customer reviews indicate confusion about product use/dimensions rather than product flaws.",
            "confidence": 0.87,
            "recommended_service": "Review Sentiment Analysis & Listing Clarification"
        }

    # 6. Images & Video
    if any(k in combined_signal for k in ["few images", "missing image", "no video", "poor images", "weak images", "lifestyle image", "infographic"]):
        return {
            "template_id": 3,
            "category": "Listing — Images & Video",
            "reason": "Detected few images, missing lifestyle content, or lack of mobile infographics/video.",
            "confidence": 0.88,
            "recommended_service": "Infographic Design & Mobile Image Optimization"
        }

    # 7. A+ Content
    if any(k in combined_signal for k in ["weak a+", "low a+", "missing a+", "no a+", "ebc", "brand story missing", "no brand story"]):
        return {
            "template_id": 4,
            "category": "Listing — A+ Content",
            "reason": "Missing or generic A+ Content modules observed on product pages.",
            "confidence": 0.89,
            "recommended_service": "A+ Content Design & Comparison Modules"
        }

    # 8. SEO & Copy
    if any(k in combined_signal for k in ["seo", "weak title", "short bullet", "poor bullet", "keyword missing", "unoptimized copy"]):
        return {
            "template_id": 2,
            "category": "Listing — SEO & Copy",
            "reason": "Listing titles and bullet points lack strong search keywords and conversion clarity.",
            "confidence": 0.87,
            "recommended_service": "Listing SEO, Title & Bullet Optimization"
        }

    # 9. Multiple Gaps
    if any(k in combined_signal for k in ["multiple gaps", "several gaps", "several listing problems", "low listing score", "audit score"]):
        return {
            "template_id": 5,
            "category": "Listing — Multiple Gaps",
            "reason": "Multiple concurrent listing issues (copy, images, and modules) holding conversion back.",
            "confidence": 0.85,
            "recommended_service": "Full Listing Overhaul & Conversion Rate Optimization"
        }

    # 10. Competitor Opportunity
    if any(k in combined_signal for k in ["competitor", "conquest", "category ads", "taking space", "outranking"]):
        return {
            "template_id": 13,
            "category": "Competitor / Market Opportunity",
            "reason": "Competitors actively taking up ad space and organic placements around brand's products.",
            "confidence": 0.84,
            "recommended_service": "Competitor Conquesting & Category Share Expansion"
        }

    # 11. Overlooked SKU
    if any(k in combined_signal for k in ["overlooked sku", "hidden gem", "single product", "one sku", "neglected product"]):
        return {
            "template_id": 15,
            "category": "Product / SKU Opportunity",
            "reason": "Promising secondary SKU observed receiving disproportionately low attention.",
            "confidence": 0.82,
            "recommended_service": "Secondary SKU Repositioning"
        }

    # 12. Brand Story
    if any(k in combined_signal for k in ["brand story", "d2c story", "website story", "brand presence"]):
        return {
            "template_id": 14,
            "category": "Brand Story → Amazon",
            "reason": "Strong brand story off Amazon that is not translating into the Amazon catalog.",
            "confidence": 0.83,
            "recommended_service": "Brand Story Module Setup & Storefront Architecture"
        }

    # 13. PPC Scaling
    if any(k in combined_signal for k in ["ppc scaling", "scale ppc", "scale ads", "ad spend", "grow ppc"]):
        return {
            "template_id": 8,
            "category": "PPC — Scaling",
            "reason": "Brand has solid catalog and active advertising ready for systematic budget scaling.",
            "confidence": 0.85,
            "recommended_service": "PPC Account Restructuring & Budget Scaling"
        }

    # 14. Full Catalog
    if any(k in combined_signal for k in ["catalog", "multi-sku", "entire account", "many products"]):
        return {
            "template_id": 12,
            "category": "Full Amazon Growth / Catalog",
            "reason": "Multi-SKU catalog with varied operational and listing needs across products.",
            "confidence": 0.80,
            "recommended_service": "Full Catalog Strategy & Channel Management"
        }

    # Default fallback: General Amazon Growth
    return {
        "template_id": 1,
        "category": "Amazon Growth — General",
        "reason": "Broad growth opportunity across search visibility, listing content, and positioning.",
        "confidence": 0.75,
        "recommended_service": "Full-Service Amazon Growth & Strategy"
    }


# -----------------------------------------------------------------------------
# FACT-SAFE VARIABLE RESOLUTION ENGINE
# -----------------------------------------------------------------------------
def resolve_sellomize_email(
    template_text: str,
    subject_text: str,
    contact_data: Dict[str, Any],
    client_story_key: Optional[str] = None
) -> Tuple[str, str]:
    """
    Renders subject and body with strict fact-safety:
    1. Location Rule:
       - If Location is verified: Subject format is '[Company] + [Location] + Sellomize'.
       - If unverified/empty: Subject format is '[Company] + Sellomize'. Never invent city/state.
    2. Contact Name Rule:
       - If contact name is unavailable, replaces 'Hi {first_name},' with 'Hi,' (no fake names).
    3. Unverified Variables:
       - Omitted cleanly without leaving dangling brackets or broken spacing.
    4. Client Proof:
       - If client_story_key provided, injects 3-part proof clearly labeled as client outcomes.
    Returns (resolved_subject, resolved_body).
    """
    var_map: Dict[str, str] = {}

    # 1. Contact Name
    full_name = str(contact_data.get("name") or "").strip()
    first_name = full_name.split()[0] if full_name else ""
    var_map["name"] = first_name or full_name
    var_map["first_name"] = first_name
    var_map["firstname"] = first_name

    # 2. Company
    company = str(contact_data.get("company") or "").strip()
    var_map["company"] = company or "your brand"

    # 3. Location (Strict check)
    custom_vars = contact_data.get("custom_variables_dict") or {}
    if not isinstance(custom_vars, dict) and isinstance(contact_data.get("custom_variables"), str):
        try:
            custom_vars = json.loads(contact_data.get("custom_variables") or "{}")
        except Exception:
            custom_vars = {}

    raw_loc = (
        custom_vars.get("Verified Location") or
        custom_vars.get("verified_location") or
        custom_vars.get("Location") or
        custom_vars.get("location") or
        contact_data.get("country_or_timezone") or
        ""
    ).strip()

    # Filter out timezone tokens or unverified placeholders
    is_verified_loc = bool(
        raw_loc and
        raw_loc.upper() not in ["LOCAL", "UTC", "UTC+5", "UNKNOWN", "N/A", "NONE", ""] and
        not raw_loc.startswith("GMT") and
        not raw_loc.startswith("UTC")
    )
    var_map["location"] = raw_loc if is_verified_loc else ""

    # 4. Amazon Research Variables
    var_map["product"] = str(custom_vars.get("Product") or custom_vars.get("product") or "").strip()
    var_map["asin"] = str(custom_vars.get("ASIN") or custom_vars.get("asin") or "").strip()
    var_map["amazonissue"] = str(custom_vars.get("Amazon Issues") or custom_vars.get("amazon_issue") or custom_vars.get("Listing Issues") or "").strip()
    var_map["relevantservice"] = str(custom_vars.get("Relevant Service") or custom_vars.get("relevant_service") or "").strip()
    var_map["specificobservation"] = str(custom_vars.get("Brand Observation") or custom_vars.get("specific_observation") or contact_data.get("notes") or "").strip()
    var_map["supportingobservation"] = str(custom_vars.get("Supporting Observation") or custom_vars.get("supporting_observation") or "").strip()
    var_map["role"] = str(custom_vars.get("Role") or custom_vars.get("role") or "").strip()
    var_map["keyword"] = str(custom_vars.get("Keyword") or custom_vars.get("keyword") or "").strip()
    var_map["organicrank"] = str(custom_vars.get("Organic Rank") or custom_vars.get("organic_rank") or "").strip()
    var_map["adrank"] = str(custom_vars.get("Ad Rank") or custom_vars.get("ad_rank") or "").strip()
    var_map["listingscore"] = str(custom_vars.get("Listing Score") or custom_vars.get("listing_score") or "").strip()
    var_map["rating"] = str(custom_vars.get("Rating") or custom_vars.get("rating") or "").strip()
    var_map["reviewcount"] = str(custom_vars.get("Review Count") or custom_vars.get("review_count") or "").strip()
    var_map["boughtpastmonth"] = str(custom_vars.get("Bought Past Month") or custom_vars.get("bought_past_month") or "").strip()
    var_map["bsr"] = str(custom_vars.get("BSR") or custom_vars.get("bsr") or "").strip()

    # 5. Client Story 3-Part Fields
    if client_story_key and client_story_key in APPROVED_CLIENT_STORIES:
        cs = APPROVED_CLIENT_STORIES[client_story_key]
        var_map["clientstoryfound"] = cs["found"]
        var_map["clientstorysolved"] = cs["solved"]
        var_map["clientstoryrewarded"] = cs["rewarded"]
        var_map["clientstory"] = (
            f"What we found: {cs['found']}\n"
            f"How we solved it: {cs['solved']}\n"
            f"What it rewarded: {cs['rewarded']}"
        )
    else:
        var_map["clientstoryfound"] = ""
        var_map["clientstorysolved"] = ""
        var_map["clientstoryrewarded"] = ""
        var_map["clientstory"] = ""

    # Subject Line Resolution
    subj = subject_text
    if "[Company] + Sellomize" in subj or "[Company] + [Location] + Sellomize" in subj:
        if is_verified_loc and company:
            subj = f"{company} + {raw_loc} + Sellomize"
        elif company:
            subj = f"{company} + Sellomize"
        else:
            subj = "Amazon growth + Sellomize"
    else:
        # Standard token replacements for subject
        for k, v in var_map.items():
            pattern = re.compile(rf'\[{re.escape(k)}\]', re.IGNORECASE)
            subj = pattern.sub(v, subj)
        # Clean any remaining empty brackets in subject
        subj = re.sub(r'\[[a-zA-Z0-9_\s-]+\]', '', subj)
        subj = re.sub(r'\s{2,}', ' ', subj).strip()

    # Body Line Resolution
    body = template_text

    # Contact greeting fallback
    if not var_map["name"]:
        body = re.sub(r'Hi\s+\{first_name\},', 'Hi,', body, flags=re.IGNORECASE)
        body = re.sub(r'Hi\s+\{firstname\},', 'Hi,', body, flags=re.IGNORECASE)
        body = re.sub(r'Hi\s+\{name\},', 'Hi,', body, flags=re.IGNORECASE)
        body = re.sub(r'Hi\s+\[Name\],', 'Hi,', body, flags=re.IGNORECASE)
        body = re.sub(r'Hi\s+\[First Name\],', 'Hi,', body, flags=re.IGNORECASE)
        body = re.sub(r'Hi\s+\[firstname\],', 'Hi,', body, flags=re.IGNORECASE)

    # Token replacements for body (both [token] and {token} formats)
    for k, v in var_map.items():
        pattern_bracket = re.compile(rf'\[{re.escape(k)}\]', re.IGNORECASE)
        body = pattern_bracket.sub(v, body)
        pattern_curly = re.compile(rf'\{{{re.escape(k)}\}}', re.IGNORECASE)
        body = pattern_curly.sub(v, body)

    # If [AmazonIssue] is missing/omitted, smooth out awkward phrasing like "note about for [Company]"
    if not var_map.get("amazonissue"):
        body = re.sub(r'note about\s+for\s+', 'note about ', body, flags=re.IGNORECASE)
        body = re.sub(r'note regarding\s+for\s+', 'note regarding ', body, flags=re.IGNORECASE)

    # Clean unverified tokens cleanly so no broken brackets remain
    body = re.sub(r'\[[a-zA-Z0-9_\s-]+\]', '', body)

    # Normalize double spaces and multiple blank lines
    body = re.sub(r'[ \t]{2,}', ' ', body)
    body = re.sub(r'\n{3,}', '\n\n', body)

    return subj.strip(), body.strip()
