from drf_spectacular.extensions import OpenApiAuthenticationExtension


class DCRDirectLoginScheme(OpenApiAuthenticationExtension):
    target_class = "apps.dcr.authentication.DCRDirectLoginAuthentication"
    name = "DCRDirectLogin"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": 'DCR DirectLogin token, e.g. `DirectLogin token="<token>"`',
        }
