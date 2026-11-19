"""Rebuild the checked-in city map from pinned open inputs and reviewed selections."""
import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ADMIN = "modood/Administrative-divisions-of-China/c49d495b40ac73eb1a66f6eeae5f8fd10696f035"
WEATHER = "wb04307201/cn-weather-mcp/62e117a781e650b5657867373117fe6a586e68eb"
CLI = "ufownl/auto-zhipin/09e6af1ab35e8dd2ea420e860fccc4b95642223c"
INPUTS = {
    "admin-provinces.json": (ADMIN + "/dist/provinces.json", "a7e6a230102fee054daa7f63d91ca8da4320c11a2ddd4e5f093999b579fd7d4f"),
    "admin-cities.json": (ADMIN + "/dist/cities.json", "3f569aaa0bfbeba72f1597657511c64f54107baae71710fef7146f390a41af32"),
    "admin-areas.json": (ADMIN + "/dist/areas.json", "fbe1575eecba4ffd4d50c3d2d6887bd873ceca6a203fb2d66698b5007826b6b6"),
    "weather-WeatherService.java": (WEATHER + "/src/main/java/cn/wubo/cn/weather/mcp/WeatherService.java", "975ae9822d248675e7fbed73f4299bedb3d374bb89eaa30c70346ab36a767e58"),
    "country-readme": (CLI + "/README.md", "357a325a4a18b0dac282838dfeaf3999b8bf92b80e9797c5bc50a4914b8b0f03"),
}


def read_inputs(directory):
    result = {}
    for name, (source, digest) in INPUTS.items():
        if directory:
            raw = (directory / name).read_bytes()
        else:
            with urlopen("https://raw.githubusercontent.com/" + source, timeout=30) as response:
                raw = response.read()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"Input checksum mismatch: {name}")
        result[name] = raw.decode("utf-8-sig")
    return result


def short_name(name):
    return name.removesuffix("地区").removesuffix("林区").removesuffix("市").removesuffix("县")


def names_match(official, station):
    # State Council decision 国函〔2010〕129号 renamed 襄樊市 to 襄阳市.
    if official == "襄阳市" and station == "襄樊":
        return True
    if short_name(official) == short_name(station):
        return True
    return official.endswith(("自治州", "自治县", "盟")) and official.startswith(station) and len(station) >= 2


def make_catalog(inputs):
    provinces = json.loads(inputs["admin-provinces.json"])
    cities = json.loads(inputs["admin-cities.json"])
    districts = json.loads(inputs["admin-areas.json"])
    stations = re.findall(r'new CityCodeInfo\(\d+, "([^"]+)", "([^"]+)", "(\d{9})"\)', inputs["weather-WeatherService.java"])
    catalog = defaultdict(dict)
    for province in provinces:
        code, name = province["code"], province["name"]
        rows = [(place, number) for region, place, number in stations if region[:2] == name[:2]]
        if not rows:
            continue
        prefixes = {number[:5] for _, number in rows}
        if len(prefixes) != 1:
            raise ValueError(f"Inconsistent province prefix for {name}")
        prefix = prefixes.pop()
        divisions = {r["code"]: r["name"] for r in cities if r["provinceCode"] == code
                     and r["name"] not in {"市辖区", "县"} and not r["name"].endswith("直辖县级行政区划")}
        divisions.update({r["code"]: r["name"] for r in districts
                          if r["provinceCode"] == code and r["cityCode"].endswith("90")})
        if not divisions and name.endswith("市"):
            divisions[code] = name
        seats = defaultdict(set)
        for r in districts:
            if r["provinceCode"] == code and r["name"].endswith(("县", "市")):
                seats[r["cityCode"]].add(short_name(r["name"]))
        station_groups = {}
        for div, full in divisions.items():
            direct = {number[:7] for station, number in rows if names_match(full, station)}
            via_district = {number[:7] for station, number in rows if short_name(station) in seats[div]}
            groups = direct or via_district
            if len(groups) == 1:
                group = groups.pop()
                station_groups[div] = group
                catalog["station-group"][div] = (full, group + "00")

        def fill_order(method, ordered):
            for index, div in enumerate(ordered, 1):
                catalog[method][div] = (divisions[div], f"{prefix}{index:02}00")

        fill_order("admin-order", sorted(divisions))
        # Group prefecture-level cities first, then other prefectures, then provincial counties.
        def level(div):
            if len(div) == 6:
                return 2
            return 0 if divisions[div].endswith("市") else 1

        fill_order("station-order", sorted(station_groups, key=lambda div: (station_groups[div], div)))
        fill_order("station-by-level", sorted(station_groups, key=lambda div: (level(div), station_groups[div], div)))
        source_order = {}
        for _, number in rows:
            source_order.setdefault(number[:7], len(source_order))
        fill_order("station-source-order", sorted(station_groups, key=lambda div: (source_order[station_groups[div]], div)))
    example = re.search(r'全国.*?/web/geek/jobs\?city=(\d{9})&', inputs["country-readme"])
    if not example:
        raise ValueError("Nationwide example missing")
    catalog["documented-url"]["national"] = ("全国", example.group(1))
    return catalog


def build(catalog, selections):
    cities = {}
    for method, entries in selections.items():
        for key, display in entries:
            full, number = catalog[method][key]
            if display not in (full, short_name(full)):
                raise ValueError(f"Unexpected display name for {full}: {display}")
            if display in cities or number in cities.values() or not re.fullmatch(r"\d{9}", number):
                raise ValueError(f"Duplicate or invalid mapping for {display}")
            cities[display] = number
    return {"source": "docs/boss-city-mapping.md", "cities": dict(sorted(cities.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, help="Use an offline directory of pinned upstream inputs")
    parser.add_argument("--output", type=Path, default=HERE.parent / "boss-cities.json")
    args = parser.parse_args()
    catalog = make_catalog(read_inputs(args.inputs))
    selections = json.loads((HERE / "selections.json").read_text("utf-8"))
    result = build(catalog, selections)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
