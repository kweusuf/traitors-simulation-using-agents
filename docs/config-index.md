# Config index

## Season ground truths (full episode/exit/recruitment details)
- the-traitors-uk-s01.yaml
- the-traitors-us-s01.yaml
- the-traitors-us-s02.yaml
- the-traitors-us-s03.yaml
- the-traitors-us-s04.yaml
- the-traitors-india-s01.yaml
- the-traitors-nz-s01.yaml
- the-traitors-nz-s02.yaml
- the-traitors-australia-s01.yaml
- the-traitors-australia-s02.yaml

## Base season run configs
- season_uk_s01.yaml (+ variants)
- season_us_s01.yaml
- season_us_s02.yaml
- season_us_s03.yaml
- season_us_s04.yaml
- season_india_s01.yaml
- season_nz_s01.yaml
- season_nz_s02.yaml
- season_australia_s01.yaml
- season_australia_s02.yaml

## Persona sets
- uk_s01 (22)
- us_s01 (20)
- us_s02 (22)
- us_s03 (23)
- us_s04 (23)
- india_s01 (20)
- nz_s01 (19)
- nz_s02 (20)
- australia_s01 (24)
- australia_s02 (20)

## Experiment arms
- season_india_s01.commandcode.yaml - India S01, original traitors pinned, on
  the Command Code Provider API (`xiaomi/mimo-v2.6-flash`); key from `.env`
- season_uk_s01.fix_en.rand.yaml - the traitor-mandate arm (mandate on)
- season_us_s02.nomandate.yaml - mandate off on a second edition; its header
  records what it does and does not control for

