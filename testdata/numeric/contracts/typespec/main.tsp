/** Independent numeric-wire fixture; not a generated production authority. */
namespace NumericWire;

model Counter {
  @minValue(0)
  @maxValue(9007199254740991)
  count: uint64;
}
