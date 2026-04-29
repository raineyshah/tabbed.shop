# tabbed.shop — Product Requirements Document

## Overview

tabbed.shop is a product discovery and filtering web app. The product catalog and site content are **internally managed**; end users do not submit products or articles.

## Stack

- **Backend:** FastAPI + SQL backend
- **Templating:** Jinja2
- **Frontend:** Vanilla JS/HTML/CSS

## Features

- Maintains a database of consumer products with rich metadata: brand, category, country of origin, price, certifications, ingredients (“made with/without”), and feature attributes
- **Catalog operations** are handled through an admin workflow (create/edit/publish products in the database)—not through public contribution flows
- Lets users filter products via a sidebar with collapsible sections for Brands, Categories, Certifications, Features, Made In, Made With, and Made Without
- Products may include affiliate links and commission flags as **site-controlled** metadata (no contributor submission or revenue-sharing model tied to user-generated listings)
- **Articles:** there is no public path for users to submit articles; any articles experience is feed- and policy-aligned with internally managed content
- **Admin panel** is used to manage the catalog (and any internal publishing workflows), not to review end-user product or article submissions
- Regardless of whether the user is logged in, the app should track client actions, such as:
  - Page visits (total)
  - Page visits per IP and per user
  - Time spent with filters applied per IP and per user
  - External buy buttons clicked per IP and per user
  - Filters clicked per IP and per user
  - Favorites per user

## Key Files

- `app.py` — FastAPI routes and business logic
- `models.py` — SQLAlchemy models (e.g. `Product`, `User`, `UserFavorite`)
- `templates/index.html` — main product browser with sidebar filtering
- Database file / schema — provisioned and hosted outside the app repo (see `TABBED_DATABASE_URL` / `models.py`)
- `schemas.py` — Pydantic schemas

## Tabbed.Shop — roadmap and operations

### Short-term roadmap

- Add user model
- In add product form, add “suggest with AI” button

### Long-term roadmap

- Add user ability to create articles using HTML in Canvas

### Logistics

- Set up Google Workspace email
- Email companies for direct affiliate programs
  - Staub
  - Dr Bronner
  - Sneeboer
  - Niwaki
  - Zwilling
- Set up Stripe Connect
- Register business with Texas
- Business banking / accounting setup (TBD)

---

## Main categories and subcategories

Planned taxonomy for navigation and filtering. Each main category has **at most nine** subcategories; labels are **single words** (including established compounds like `Tableware`) unless a one-word option would be misleading.

- **Home**
  - Furniture
  - Bedding
  - Lighting
  - Decor
  - Storage
  - Cleaning
  - Laundry
  - Climate
  - Automation
- **Garden**
  - Seeds
  - Tools
  - Irrigation
  - Soil
  - Plantcare
  - Structures
  - Patio
  - Composting
  - Planters
- **Kitchen**
  - Cookware
  - Cutlery
  - Utensils
  - Boards
  - Organization
  - Appliances
  - Tableware
  - Serving
  - Storage
- **DIY**
  - Handtools
  - Powertools
  - Paints
  - Woodworking
  - Hardware
  - Electrical
  - Plumbing
  - Safety
  - Adhesives
- **Travel**
  - Luggage
  - Carryons
  - Backpacks
  - Organizers
  - Accessories
  - Toiletries
  - Wallets
  - Power
  - Outdoors
- **Clothing**
  - Tops
  - Bottoms
  - Dresses
  - Outerwear
  - Footwear
  - Underwear
  - Activewear
  - Sleepwear
  - Accessories
- **Wellness**
  - Supplements
  - Skincare
  - Hygiene
  - Oralcare
  - Fitness
  - Sleep
  - Firstaid
  - Nutrition
  - Recovery
- **Food**
  - Pantry
  - Beverages
  - Snacks
  - Baking
  - Condiments
  - Frozen
  - Refrigerated
  - Specialty
- **Children**
  - Nursery
  - Feeding
  - Diapering
  - Bath
  - Gear
  - Safety
  - Toys
  - School
  - Apparel

