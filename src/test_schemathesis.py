import schemathesis

schema = schemathesis.openapi.from_url(
    "http://localhost:8080/api/v3/openapi.json",
)

@schema.parametrize()
def test_api(case):
    case.call_and_validate()