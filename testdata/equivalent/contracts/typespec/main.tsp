import "@typespec/json-schema";
using TypeSpec.JsonSchema;

namespace OresChatTest;

@id("Status")
enum Status {
  on: "on",
  off: "off",
}

@id("Widget")
model Widget {
  name: string;
  status: Status;

  @minValue(0)
  count?: int32;
}
