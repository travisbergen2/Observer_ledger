// prng_check.mjs — the page's PRNG, run under Node, to confirm it is bit-identical to observer_twin.Mulberry32
function mulberry32(seed) {
  var a = seed | 0;
  return function () {
    a = (a + 0x6D2B79F5) | 0;
    var t = Math.imul(a ^ (a >>> 15), a | 1);
    t = (t + Math.imul(t ^ (t >>> 7), t | 61)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function irwinHall(r) { var s = 0; for (var i = 0; i < 12; i++) s += r(); return s - 6; }
var r = mulberry32(12345);
var out = [r(), r(), r()];
var r2 = mulberry32(7);
for (var i = 0; i < 1000; i++) r2();
out.push(r2());
out.push(irwinHall(mulberry32(99)));
console.log(JSON.stringify(out));
