"""
=============================================================================
Knowledge Base Module (knowledge_base.py)
=============================================================================
Provides a structured domain knowledge base and search engine for:
1. System Architecture & Multi-Store Fulfillment Rules
2. Safety Stock Buffer Policy (why a 15% default is held)
3. Regional Store Locations, Hubs, and Delivery Timelines
4. Product Catalog Details & Material Specifications
5. Order Release, Invoicing, and Payment Terms
=============================================================================
"""

import re
from typing import Dict, List, Any, Optional


class KnowledgeBase:
    """
    In-memory knowledge repository with keyword and semantic intent matching.
    """

    def __init__(self):
        self.articles = self._load_articles()

    def _load_articles(self) -> List[Dict[str, Any]]:
        """Defines all domain knowledge articles and FAQ entries."""
        return [
            # -----------------------------------------------------------------
            # 1. SAFETY STOCK & BUFFER POLICY
            # -----------------------------------------------------------------
            {
                "id": "safety_stock_policy",
                "category": "Policy & Operations",
                "title": "Safety Stock Buffer Policy",
                "keywords": [
                    "safety stock", "buffer", "reserve", "percentage", "safety buffer",
                    "why reserve", "protection", "retail clients", "shortage", "buffer calculation"
                ],
                "summary": (
                    "**Safety Stock Buffer Policy (10% – 30%):**\n\n"
                    "- **Purpose:** To ensure no single bulk client order depletes a regional store's entire stock, "
                    "a dynamic safety buffer (default 15%, adjustable between 10%–30%) is reserved in every store.\n"
                    "- **Formula:**\n"
                    "  - `Safety Buffer = ceil(Total Store Stock * Safety %)`\n"
                    "  - `Salable Stock = max(0, Total Store Stock - Safety Buffer)`\n"
                    "- **Protection:** Reserved units remain untouched for emergency retail walk-ins and local operations, "
                    "preventing complete stockouts."
                )
            },


            # -----------------------------------------------------------------
            # 2. MULTI-STORE CASCADING FULFILLMENT
            # -----------------------------------------------------------------
            {
                "id": "cascading_allocation",
                "category": "Policy & Operations",
                "title": "Multi-Store Cascading Order Fulfillment",
                "keywords": [
                    "cascade", "cascading", "multi-store", "split", "split order",
                    "two stores", "multiple stores", "fallback", "insufficient stock",
                    "how allocation works", "allocation rule"
                ],
                "summary": (
                    "**Multi-Store Cascading Order Fulfillment:**\n\n"
                    "- **How it works:** When a client's requested quantity exceeds the salable stock of the primary store, "
                    "the system automatically pulls available stock from the primary store and cascades the remaining "
                    "balance to the next store with the highest available units.\n"
                    "- **Goal:** Achieve 100% order fulfillment automatically without manual intervention or order rejection.\n"
                    "- **Transparency:** Both the chat assistant and the generated release documents show the exact quantity "
                    "dispatched from each regional store."
                )
            },

            # -----------------------------------------------------------------
            # 3. REGIONAL STORE LOCATIONS & HUBS
            # -----------------------------------------------------------------
            {
                "id": "store_locations",
                "category": "Stores & Logistics",
                "title": "Regional Store Network & Hub Locations",
                "keywords": [
                    "stores", "locations", "branches", "hubs", "where are stores",
                    "karachi", "lahore", "faisalabad", "rawalpindi", "multan",
                    "peshawar", "sialkot", "gujranwala", "cities", "depots"
                ],
                "summary": (
                    "**Regional Store Network (10 Nationwide Hubs):**\n\n"
                    "1. **Karachi Central:** Main Port & Coastal Province Hub (Sindh)\n"
                    "2. **Karachi Korangi:** Industrial Area Depot (Sindh)\n"
                    "3. **Lahore Badami Bagh:** Central Punjab Main Distribution Center\n"
                    "4. **Lahore Gulberg:** Commercial & Retail Hub (Punjab)\n"
                    "5. **Faisalabad Hub:** Textile Capital Mega-Warehouse (Largest Stock)\n"
                    "6. **Rawalpindi Hub:** Northern Punjab & Capital Region Hub\n"
                    "7. **Multan Depot:** Southern Punjab Logistics Center\n"
                    "8. **Peshawar Store:** Khyber Pakhtunkhwa Regional Depot\n"
                    "9. **Sialkot Hub:** Export & Industrial Manufacturing Zone\n"
                    "10. **Gujranwala Outlet:** Upper Punjab Distribution Point"
                )
            },

            # -----------------------------------------------------------------
            # 4. DELIVERY & SHIPPING TIMELINES
            # -----------------------------------------------------------------
            {
                "id": "delivery_lead_times",
                "category": "Stores & Logistics",
                "title": "Delivery & Shipping Lead Times",
                "keywords": [
                    "delivery", "shipping", "lead time", "transit", "dispatch time",
                    "how long", "delivery time", "arrival", "freight", "transport"
                ],
                "summary": (
                    "**Shipping & Delivery Lead Times:**\n\n"
                    "- **Intra-City Dispatch:** Same-day or within 24 hours (e.g. within Karachi or Lahore limits).\n"
                    "- **Inter-City / Regional Transit:** 24 to 48 hours via dedicated logistics freight.\n"
                    "- **Multi-Store Consolidated Delivery:** When goods are dispatched from two different stores, "
                    "shipments may arrive in two separate consignments depending on client preference."
                )
            },


            # -----------------------------------------------------------------
            # 5. PRODUCT CATALOG & CATEGORIES
            # -----------------------------------------------------------------
            {
                "id": "product_catalog",
                "category": "Catalog & Materials",
                "title": "Product Catalog & Specifications",
                "keywords": [
                    "products", "catalog", "items", "categories", "fabric", "yarn",
                    "denim", "accessories", "packaging", "materials", "what do you sell"
                ],
                "summary": (
                    "**Master Product Catalog (22 Products across 5 Categories):**\n\n"
                    "- **Fabrics:** Cotton Fabric Roll 100m, Silk Fabric 50m, Linen Shirt Fabric, Woolen Blend 50m, "
                    "Poplin Grey 120m, Lawn Printed Premium, Chiffon Dyed, Georgette, Viscose Rayon 100m.\n"
                    "- **Yarn:** Polyester Yarn 24kg Box, Spun Yarn Cone 50s, Cotton Combed Yarn 20kg, Nylon Filament Thread.\n"
                    "- **Denim:** Denim Jeans Fabric Roll, Stretch Twill Denim, Heavyweight Raw Denim 14oz.\n"
                    "- **Accessories:** Lycra Elastic Ribbon Box, Metal Zipper 24-inch Box, Polyester Sewing Thread Box.\n"
                    "- **Packaging:** Corrugated Packing Carton XL, Bubble Wrap Roll 100m, Industrial Stretch Film Roll."
                )
            },


            # -----------------------------------------------------------------
            # 6. ORDER RELEASE WORKFLOW & FILES
            # -----------------------------------------------------------------
            {
                "id": "order_release_workflow",
                "category": "System Workflow",
                "title": "Order Release & Documentation Workflow",
                "keywords": [
                    "release", "workflow", "order confirmation", "files", "excel",
                    "sheets", "deduction", "what happens on confirm", "audit"
                ],
                "summary": (
                    "**Order Release & Confirmation Workflow:**\n\n"
                    "1. **Inventory Deduction:** Confirmed quantities are taken from the allocated stores. The uploaded "
                    "stock report in the `data` folder is never edited; stock is shown as the report minus released orders.\n"
                    "2. **Order File:** One Excel file is created in the `orders` folder for each order "
                    "(`orders/Order_{Order_ID}_{Party}.xlsx`). It lists the product, store and quantity released.\n"
                    "3. **History:** The Released Orders History tab is built from these order files."
                )
            },

            # -----------------------------------------------------------------
            # 7. ORDER CANCELLATION POLICY
            # -----------------------------------------------------------------
            {
                "id": "cancellation_policy",
                "category": "Policy & Operations",
                "title": "Order Cancellation Policy",
                "keywords": [
                    "cancel", "cancellation", "abort", "refund", "stop order",
                    "can i cancel", "cancel released order"
                ],
                "summary": (
                    "**Order Cancellation Policy:**\n\n"
                    "- **Prior to Confirmation:** You can cancel any proposed allocation plan anytime by clicking **'Cancel Order'** "
                    "or typing *'cancel'* in the chat.\n"
                    "- **After Release Confirmation:** Once an order has been confirmed and released, its Excel file is created "
                    "in the `orders` folder and its units are taken from stock. If the party cancels, open the "
                    "**'Released Orders History'** tab and use **'Delete a Released Order'**. This removes the order's Excel file "
                    "and returns the units to stock (as long as the stock report loaded when the order was placed is still the current one)."
                )
            },

            # -----------------------------------------------------------------
            # 8. PAYMENT & COMMERCIAL TERMS
            # -----------------------------------------------------------------
            {
                "id": "payment_terms",
                "category": "Commercial Terms",
                "title": "Commercial & Payment Terms",
                "keywords": [
                    "payment", "price", "terms", "credit", "invoice", "cost", "billing"
                ],
                "summary": (
                    "**B2B Commercial & Payment Terms:**\n\n"
                    "- **Unit Pricing:** All pricing reflects standard wholesale B2B rates specified in the master catalog.\n"
                    "- **Payment Methods:** Bank wire transfer, Pay Order, or corporate credit terms (Net-30) for approved parties.\n"
                    "- **Release Condition:** Release documents indicate Total Order Value for finance clearance."
                )
            }
        ]


    def search(self, query: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Searches the knowledge base using keyword overlap and question intent matching.

        Args:
            query (str): User question or query.
            context (dict, optional): Current conversation context.

        Returns:
            dict or None: Best matching article dictionary with score, or None if no match.
        """
        clean_query = query.lower().strip()
        query_tokens = set(re.findall(r'\b[a-zA-Z]{3,}\b', clean_query))

        if not query_tokens:
            return None

        best_article = None
        highest_score = 0

        for article in self.articles:
            score = 0
            # Check match against article keywords
            for kw in article["keywords"]:
                kw_lower = kw.lower()
                if kw_lower in clean_query:
                    # Multi-word exact phrase match gets high score
                    score += 4 if " " in kw_lower else 2
                else:
                    kw_tokens = set(kw_lower.split())
                    common = query_tokens.intersection(kw_tokens)
                    score += len(common)

            # Check match against title
            title_tokens = set(re.findall(r'\b[a-zA-Z]{3,}\b', article["title"].lower()))
            score += len(query_tokens.intersection(title_tokens)) * 2

            if score > highest_score:
                highest_score = score
                best_article = article

        # Threshold to ensure relevance
        if best_article and highest_score >= 2:
            return {
                "article": best_article,
                "score": highest_score,
                "formatted_response": f"📖 **{best_article['title']}**\n\n{best_article['summary']}"
            }

        return None