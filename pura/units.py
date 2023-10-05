import pint
from pydantic import BaseModel,GetJsonSchemaHandler
from pydantic_core import CoreSchema
from typing import Any, Dict

ureg = pint.UnitRegistry()


def quantity(dimensionality: str) -> type:
    """A method for making a pydantic compliant Pint quantity field type."""

    try:
        ureg.get_dimensionality(dimensionality)
    except KeyError:
        raise ValueError(f"{dimensionality} is not a valid dimensionality in pint!")

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, value):
        quantity = pint.Quantity(value)
        if not quantity.check(cls.dimensionality):
            raise ValueError(
                f"Dimensionality must be {cls.dimensionality}. Currently {quantity.dimensionality}."
            )
        return quantity
    
    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> Dict[str, Any]:
        json_schema = super(pint.Quantity, cls).__get_pydantic_json_schema__(core_schema, handler)
        json_schema = handler.resolve_ref_schema(json_schema)
        json_schema.update({"$ref": f"#/definitions/Quantity{dimensionality}"})
        return json_schema
    # @classmethod
    # def __modify_schema__(cls, field_schema):
    #     field_schema.update({"$ref": f"#/definitions/Quantity{dimensionality}"})

    return type(
        "Quantity",
        (pint.Quantity,),
        dict(
            __get_validators__=__get_validators__,
            __get_pydantic_json_schema__=__get_pydantic_json_schema__,
            dimensionality=dimensionality,
            validate=validate,
        ),
    )


# Dimensions
Mass = quantity("[mass]")
Amount = quantity("[substance]")
Volume = quantity("[volume]")
Time = quantity("[time]")
Temperature = quantity("[temperature]")
Pressure = quantity("[pressure]")
MassFlow = quantity("[mass]/[time]")
VolumeFlow = quantity("[volume]/[time]")
MolarFlow = quantity("[substance]/[time]")


class PintModel(BaseModel):
    class Config:
        validate_assignment = True
        # schema_extra = {
        #     "definitions": [
        #         {
        #             pint.Quantity: dict(type="string"),
        #         }
        #     ]
        # }
        json_encoders = {
            pint.Quantity: str,
        }


__all__ = [
    "Mass",
    "Amount",
    "Volume",
    "Time",
    "Temperature",
    "Pressure",
    "PintModel",
    "MassFlow",
    "VolumeFlow",
    "MolarFlow",
    "ureg",
]
