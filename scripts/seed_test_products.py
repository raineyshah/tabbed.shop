#!/usr/bin/env python3
"""Seed fake test products across several categories."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import json

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "products.db")

PRODUCTS = [
    # Home / Furniture
    dict(product_name="Oak Dining Table", brand_name="Woodland Co.", main_category="Home", subcategory="Furniture",
         made_in="USA", price=499.00, affiliate_link="https://example.com/oak-table", earns_commission=1,
         made_with=json.dumps(["Solid Oak"]), made_without=json.dumps(["MDF","Formaldehyde"]),
         attributes=json.dumps(["Solid Wood","Handcrafted"]), description="A sturdy handcrafted oak dining table.", is_verified=1),
    dict(product_name="Linen Accent Chair", brand_name="Comfort Studio", main_category="Home", subcategory="Furniture",
         made_in="Canada", price=289.00, affiliate_link="https://example.com/linen-chair", earns_commission=0,
         made_with=json.dumps(["Linen","Reclaimed Wood"]), made_without=json.dumps(["PVC"]),
         attributes=json.dumps(["Eco-Friendly","FSC Certified Wood"]), description="Breathable linen accent chair.", is_verified=1),
    dict(product_name="Ceramic Table Lamp", brand_name="Glow Works", main_category="Home", subcategory="Decor & lighting",
         made_in="Portugal", price=89.00, affiliate_link="https://example.com/lamp", earns_commission=1,
         made_with=json.dumps(["Ceramic","Cotton Cord"]), made_without=json.dumps(["Plastic"]),
         attributes=json.dumps(["Handmade"]), description="Handthrown ceramic table lamp.", is_verified=1),

    # Kitchen
    dict(product_name="Cast Iron Skillet 10\"", brand_name="Lodge", main_category="Kitchen", subcategory="Cookware",
         made_in="USA", price=39.95, affiliate_link="https://example.com/cast-iron", earns_commission=1,
         made_with=json.dumps(["Cast Iron"]), made_without=json.dumps(["Non-stick coating","PFAS"]),
         attributes=json.dumps(["Pre-seasoned","Oven Safe"]), description="Classic pre-seasoned cast iron skillet.", is_verified=1),
    dict(product_name="Glass Food Storage Set 5pc", brand_name="GreenGuard", main_category="Kitchen", subcategory="Food storage",
         made_in="Germany", price=54.00, affiliate_link="https://example.com/glass-storage", earns_commission=0,
         made_with=json.dumps(["Borosilicate Glass","Bamboo Lids"]), made_without=json.dumps(["BPA","Plastic Lids"]),
         attributes=json.dumps(["Dishwasher Safe","Microwave Safe"]), description="Borosilicate glass containers with bamboo lids.", is_verified=1),
    dict(product_name="Bamboo Cutting Board", brand_name="EcoCut", main_category="Kitchen", subcategory="Utensils & gadgets",
         made_in="China", price=24.99, affiliate_link="https://example.com/bamboo-board", earns_commission=1,
         made_with=json.dumps(["Bamboo"]), made_without=json.dumps(["Plastic","Glue additives"]),
         attributes=json.dumps(["Sustainable","Antibacterial"]), description="End-grain bamboo cutting board.", is_verified=0),

    # Clothing
    dict(product_name="Organic Cotton Tee", brand_name="Pure Roots", main_category="Clothing", subcategory="Tops",
         made_in="India", price=38.00, affiliate_link="https://example.com/organic-tee", earns_commission=1,
         made_with=json.dumps(["100% GOTS Organic Cotton"]), made_without=json.dumps(["Synthetic dyes","Pesticides"]),
         attributes=json.dumps(["GOTS Certified","Fair Trade"]), description="Soft GOTS-certified organic cotton tee.", is_verified=1),
    dict(product_name="Recycled Wool Sweater", brand_name="ReWool", main_category="Clothing", subcategory="Tops",
         made_in="Italy", price=145.00, affiliate_link="https://example.com/wool-sweater", earns_commission=0,
         made_with=json.dumps(["Recycled Wool","Recycled Cashmere"]), made_without=json.dumps(["Virgin Wool","Acrylic"]),
         attributes=json.dumps(["Recycled Materials","RWS Certified"]), description="Luxuriously soft recycled wool sweater.", is_verified=1),
    dict(product_name="Natural Rubber Rain Boots", brand_name="Earthstep", main_category="Clothing", subcategory="Shoes",
         made_in="UK", price=95.00, affiliate_link="https://example.com/rain-boots", earns_commission=1,
         made_with=json.dumps(["Natural Rubber"]), made_without=json.dumps(["PVC","Synthetic Rubber"]),
         attributes=json.dumps(["Waterproof","Vegan"]), description="Classic natural rubber rain boots.", is_verified=1),

    # Body
    dict(product_name="Rosehip Face Oil 30ml", brand_name="Bloom Naturals", main_category="Body", subcategory="Skin care",
         made_in="Australia", price=42.00, affiliate_link="https://example.com/rosehip-oil", earns_commission=1,
         made_with=json.dumps(["Organic Rosehip Oil","Vitamin E"]), made_without=json.dumps(["Silicones","Parabens","Synthetic Fragrance"]),
         attributes=json.dumps(["Certified Organic","Cruelty-Free","Vegan"]), description="Pure cold-pressed rosehip face oil.", is_verified=1),
    dict(product_name="Shampoo Bar – Citrus", brand_name="BareCo", main_category="Body", subcategory="Hair care",
         made_in="UK", price=12.00, affiliate_link="https://example.com/shampoo-bar", earns_commission=0,
         made_with=json.dumps(["Coconut Oil","Lemon Essential Oil","Castor Oil"]),
         made_without=json.dumps(["SLS","Plastic packaging","Parabens"]),
         attributes=json.dumps(["Zero Waste","Vegan","Cruelty-Free"]), description="Concentrated plastic-free shampoo bar.", is_verified=1),

    # Food
    dict(product_name="Raw Wildflower Honey 16oz", brand_name="Hive & Home", main_category="Food", subcategory="Pantry staples",
         made_in="USA", price=18.00, affiliate_link="https://example.com/honey", earns_commission=1,
         made_with=json.dumps(["Raw Honey"]), made_without=json.dumps(["Added Sugar","Pasteurization"]),
         attributes=json.dumps(["Raw","Non-GMO","Unfiltered"]), description="Single-source raw wildflower honey.", is_verified=1),
    dict(product_name="Cold Brew Coffee Concentrate", brand_name="Slow Drip Co.", main_category="Food", subcategory="Beverages",
         made_in="USA", price=16.00, affiliate_link="https://example.com/cold-brew", earns_commission=0,
         made_with=json.dumps(["Organic Coffee","Filtered Water"]), made_without=json.dumps(["Additives","Preservatives"]),
         attributes=json.dumps(["Organic","Fair Trade","Low Acid"]), description="Rich organic cold brew concentrate.", is_verified=1),
]

def seed():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Remove existing test products (those with brand matching our seed set)
    seed_brands = list({p['brand_name'] for p in PRODUCTS})
    placeholders = ','.join('?' * len(seed_brands))
    c.execute(f"DELETE FROM products WHERE brand_name IN ({placeholders})", seed_brands)
    print(f"Cleared {c.rowcount} old seed products.")

    inserted = 0
    for p in PRODUCTS:
        c.execute("""
            INSERT INTO products
                (product_name, brand_name, main_category, subcategory, made_in, price,
                 affiliate_link, earns_commission, made_with, made_without, attributes,
                 description, is_verified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p['product_name'], p['brand_name'], p['main_category'], p['subcategory'],
            p['made_in'], p['price'], p['affiliate_link'], p['earns_commission'],
            p['made_with'], p['made_without'], p['attributes'], p['description'], p['is_verified'],
        ))
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Seeded {inserted} test products.")

if __name__ == '__main__':
    seed()
