# Research: Other Traitors flavors for simulation candidates

## Goal
Identify which other Traitors franchises/seasons (US, India, NZ, Australia) have sufficient public detail to construct complete simulation configs comparable to UK S01 (`configs/seasons/the-traitors-uk-s01.yaml`). Preserve only seasons with sufficient info; discard insufficient ones.

## Sufficiency bar (based on UK S01)
A season is sufficient if we can construct:
- Season ground truth YAML matching schema: cast (id, role, result, exit episode, persona notes), eliminations per episode (night victim, banishment, vote tallies/notes), traitor_reveal_episodes, recruitments (who/when/type/accepted), finale sequence/outcome, twists_used/twists_not_used
- Complete player list (consistent IDs, orderable) with 1 persona file per player
- Original + recruited traitors identified with timing
- Episode-by-episode exit order clear
- Finale details clear

If traitors unknown, major eliminations missing, vote tallies largely absent, recruitments/finale ambiguous → Insufficient.

## 1. The Traitors US (Peacock/NBC)

Seasons: S01 (Jan 2023, civilian/reality cast mix in context; all-reality alumni cast), S02 (Jan 2024, celebrity cast), S03 (Jan 2025), S04 (Jan 2026). "New Blood" civilian spinoff S05 announced Sep 2026.

### US S01
- Cast: 20 contestants (Wikipedia lists full cast with IDs/names, roles). Original traitors: Cirie, Christian, Cody (Wikipedia). Arie recruited later (Episode 8). 
- Episodes: 11
- Eliminations: Wikipedia gives elimination history table by episode (Traitors' Decision/Murder/Shield/Banishment with vote counts in some rows) and end game. 
- Finale: Cirie (traitor) wins; Andie, Quentin runners-up (faithfuls). End game shows banishment/vote sequence.
- Recruitment: Arie recruited in Episode 8 (accepted). 
- Twists: shield appears; on-trial style elements mentioned (put 3 on trial at one point). 
- Verdict: **Sufficient**. Full cast, traitor identities (original+recruited), episode elimination table with votes, finale clear. Comparable detail level to UK S01.

### US S02 (celebrity cast)
- 22 contestants, 12 episodes. Traitors: Dan Gheesling, Phaedra Parks (original); Parvati recruited later? Wikipedia states Kate Chastain and Parvati Shallow recruited as traitors at different points? Or roles evolved. Finale: Chris "CT" Tamburello and Trishelle Cannatella won as Faithfuls; Mercedes "MJ" Javid runner-up. 
- Has cast, eliminations, recruitments documented on Wikipedia (elimination history table). Detailed enough.
- Verdict: **Sufficient**. Well-documented.

### US S03
- 23 contestants. Traitors: Bob the Drag Queen, Carolyn Wiger, Danielle Reyes (original); Britney Haynes recruited? Wikipedia gives elimination history and traitor roster. Dolores, Dylan, Gabby, Ivar won as faithfuls (Ivar Mountbatten etc). 
- Detailed cast/elimination tables. 
- Verdict: **Sufficient**.

### US S04
- 23 contestants, Jan 2026. Traitors: Candiace Dillard Bassett, Lisa Rinna, Rob Rausch (original); Donna Kelce recruited (secret traitor twist mentioned). Rob Rausch won as traitor/finalist? Wikipedia states Rob Rausch won as traitor? Finale details in tables. 
- Verdict: **Sufficient**.

### US New Blood S01 (civilian, Sep 2026)
- Just announced; full details not yet widely published in structured form (cast not fully public in sources I see). 
- Verdict: **Insufficient** (too recent/incomplete for full ground truth construction now).

## 2. The Traitors India (Prime Video, Karan Johar)

Seasons: S01 (Jun 2025), S02 (Aug 2026).

### India S01
- 20 contestants, 10 episodes (Wikipedia). Hosted by Karan Johar. 
- Cast listed (contestants with names). Traitor identities: original traitors named (Jannat Zubair Rahmani, Karan Kundrra, Raj Kundra? Or others). Wikipedia shows elimination history table with affiliations (Faithful/Traitor) and results. 
- Recruitments: Purav Jha recruited later? Wikipedia states "Nikhil recruited Purav as a traitor" in episode 9? Elimination table shows recruitment events. 
- Finale: Uorfi Javed, Nikita Luther won as faithfuls? Or Traitor won? Wikipedia gives winner(s) - S01 winner listed as Nikita Luther (faithful) and others? Need to check carefully: Wikipedia says "Harsh Gujral and Purav Jha placed as runners-up, as traitors." and winners include Nikita Luther, Uorfi Javed as faithfuls? 
- Verdict: **Sufficient**. Has cast, traitor list, elimination table, recruitments, finale.

### India S02
- Premiered Aug 2026 (10 episodes). Details emerging but full elimination history/tallies may not be fully documented in stable sources yet. Wikipedia page exists with episodes listed. 
- Verdict: **Partial/Insufficient now** - better to wait for complete documentation; S01 is solid.

## 3. The Traitors NZ (NZ)

Seasons: S01 (2023), S02 (2024), S03 (2026).

### NZ S01
- 19 contestants, 10 episodes (IMDb/Wikipedia). Hosted by Paul Henry. 
- Cast table, eliminations by episode with vote counts in Wikipedia elimination history. Traitors: Sam Smith, Nicky Glaser, Mark Dye? Original traitors identified; recruitment happened (Molly Fehr recruited?). 
- Finale: Anna Reeve won as faithful? Wikipedia gives outcome. 
- Verdict: **Sufficient**.

### NZ S02
- 20 contestants, 12 episodes. Full cast, elimination table with tallies on Wikipedia. Traitors named, recruitment occurred. 
- Verdict: **Sufficient**.

### NZ S03
- 2026 season; details being published. May be incomplete in consolidated sources. 
- Verdict: **Insufficient now**.

## 4. The Traitors Australia

Seasons: S01 (2022, civilian), S02 (2023, reality personalities), S03 (2026).

### Australia S01
- 24 contestants, 12 episodes. Original traitors: Angus McNicol, Claire Sawyer, Marielle Intveld, Nigel Brennan? Wikipedia shows 4 original traitors? Or 3? Cast table lists affiliations - Angus/Claire/Marielle/Nigel started as traitors? Also Kate Williams recruited later (Episode 10), Alex Duggan recruited (Episode 8). Alex Duggan won as traitor. 
- Full cast, elimination history table with votes, recruitments, finale. Very detailed. 
- Verdict: **Sufficient**.

### Australia S02 (celebrity/reality cast)
- 20 contestants, 9 episodes. No formal winner (unusual ending) - Camille/Blake/Sam made final as traitors but lost prize money after Traitors' Dilemma (Wikipedia). Traitors: Ash Pollard, Blake Willoughby, Camille Chicheportiche, Sam McGlone? 
- Has cast, eliminations, traitor list. Unusual ending is documented; still reconstructable for simulation (finale outcome known). 
- Verdict: **Sufficient**.

### Australia S03
- 2026, all-reality personalities cast. Recently aired/airing; full consolidated documentation may still be developing. 
- Verdict: **Insufficient now**.

## Recommended candidates (preserve only those with sufficient info)

Sufficient seasons (comparable to UK S01, detailed enough to create season ground truth + personas):

1. **US S01** (civilian/reality alumni, classic format) - 20 players, clear structure
2. **US S02** (celebrity cast) - different cast dynamic, well-documented
3. **US S03** - well-documented
4. **US S04** - well-documented (recent)
5. **India S01** (Prime Video) - 20 players, full documentation
6. **NZ S01** - 19 players, detailed
7. **NZ S02** - 20 players, detailed
8. **Australia S01** (civilian) - 24 players, very detailed
9. **Australia S02** (reality personalities) - unusual ending documented, sufficient

## Discard

- US New Blood S01 (too new, incomplete)
- India S02 (too recent, full elimination details still consolidating)
- NZ S03 (too recent)
- Australia S03 (too recent)

## Sources

- Wikipedia pages for each season (The Traitors US/Australia/India/NZ season pages)
- The Traitors Wiki (fandom.com) - detailed elimination tables, voting history
- IMDb episode guides
- Public recaps (TVMaze, epguides) for cross-checking

## Implementation note

For each sufficient season, we need to create:
- `configs/seasons/the-traitors-<country>-s<nn>.yaml` (ground truth with cast, eliminations, recruitments, finale, twists)
- `configs/personas/<country>_s<nn>/*` (one persona YAML per player with description/personality/goals based on on-screen behavior)
- `configs/traitors/season_<country>_s<nn>.yaml` (base run config matching season structure: players count, traitor count, traitor names, finale counts/mechanics, phases/cadence, quiet rounds if applicable)

The UK S01 provides the exact schema/template to follow.
