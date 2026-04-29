from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional


class ProductCreate(BaseModel):
    product_name: str
    brand_name: str
    category: str
    made_in: str
    price: float
    product_link: Optional[str] = None
    earns_commission: bool = False
    made_with: List[str] = Field(default_factory=list)
    made_without: List[str] = Field(default_factory=list)
    attributes: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    is_verified: bool = False


class ProductResponse(BaseModel):
    """Intended JSON shape for public product list items; `name` maps from ORM column `product_name`."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    name: str = Field(validation_alias="product_name")
    brand_name: str
    category: str
    made_in: str
    price: float
    product_link: Optional[str] = None
    earns_commission: bool = False
    made_with: List[str] = Field(default_factory=list)
    made_without: List[str] = Field(default_factory=list)
    attributes: List[str] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    product_image_filename: Optional[str] = None
    brand_image_filename: Optional[str] = None
    description: Optional[str] = None
    is_verified: bool = False

