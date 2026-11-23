"""
Replace every "How many Grammy Awards/nominations ...?" question in track_cache.json
with a lighter band/pop-culture/US-news question (Mark, 2026-11-17: the Grammy-count
questions fall flat and feel nerdy).

Usage:  python tools/replace_grammy_count_questions.py [path/to/track_cache.json] [--dry]
Safe to re-run: it only touches questions whose text still matches
"how many grammy" AND whose song is in REPLACEMENTS (matched by track-key prefix).
A timestamped backup of the cache is written next to it first.
Stop the app before running it on a live machine (the app rewrites the cache).
"""
import json
import os
import random
import re
import shutil
import sys
import time
import uuid

PAT = re.compile(r"how many grammy", re.I)

# (track_key prefix, question, right answer, [3 wrong answers], headline, full)
R = [
 ("swedish house mafia, john martin - don't you worry", "How many members were in Swedish House Mafia?", "3", ["2", "4", "5"],
  "Three Swedes, one big trio", "Swedish House Mafia was a trio of Axwell, Steve Angello and Sebastian Ingrosso, and 'Don't You Worry Child' became their biggest hit."),
 ("chris brown - forever", "Who was elected U.S. president the year 'Forever' came out (2008)?", "Barack Obama", ["John McCain", "Hillary Clinton", "Mitt Romney"],
  "Released in an election year", "'Forever' came out in 2008, the year Barack Obama beat John McCain to become the 44th U.S. president."),
 ("lady gaga, colby o'donis - just dance", "Which New York City university did Lady Gaga attend before dropping out to chase music?", "NYU", ["Columbia", "Juilliard", "Fordham"],
  "Gaga's college days", "Lady Gaga studied at NYU's Tisch School of the Arts before leaving to pursue music, and 'Just Dance' was her breakthrough."),
 ("toto - africa", "Which band covered 'Africa' in 2018 after a viral fan campaign?", "Weezer", ["Green Day", "Foo Fighters", "Fall Out Boy"],
  "Weezer answers the internet", "A fan's tweet campaign got Weezer to cover Toto's 'Africa' in 2018, and the cover became a hit of its own."),
 ("milli vanilli - blame it on the rain", "What shocking secret about Milli Vanilli came out in 1990?", "They didn't sing on their own album", ["They stole the songs", "They were secretly brothers", "They'd faked their ages"],
  "The biggest lip-sync scandal ever", "In 1990 it came out that Milli Vanilli hadn't sung on their own hit album, and the duo was stripped of their Best New Artist Grammy."),
 ("sarah mclachlan - building a mystery", "Sarah McLachlan founded which all-female touring music festival?", "Lilith Fair", ["Lollapalooza", "Warped Tour", "Woodstock '94"],
  "She built a festival too", "Sarah McLachlan started Lilith Fair in 1997, a touring festival that showcased women artists."),
 ("ray charles - i've got a woman", "Which actor won an Oscar for playing Ray Charles in the 2004 film 'Ray'?", "Jamie Foxx", ["Will Smith", "Denzel Washington", "Don Cheadle"],
  "Ray Charles on the big screen", "Jamie Foxx won the Best Actor Oscar for playing Ray Charles in the 2004 biopic 'Ray'."),
 ("men at work - who can it be now", "What country are Men at Work from?", "Australia", ["New Zealand", "England", "Canada"],
  "Aussie new-wave", "Men at Work came from Melbourne, Australia, and also gave the world 'Down Under'."),
 ("barry manilow - copacabana", "Who actually wrote Barry Manilow's hit 'I Write the Songs'?", "Bruce Johnston of the Beach Boys", ["Brian Wilson", "Neil Diamond", "Elton John"],
  "The song he didn't write", "'I Write the Songs' was written by Bruce Johnston of the Beach Boys, not by Barry Manilow."),
 ("michael jackson - thriller", "Which horror-movie legend delivers the creepy spoken part in 'Thriller'?", "Vincent Price", ["Boris Karloff", "Christopher Lee", "Bela Lugosi"],
  "That evil laugh", "Horror star Vincent Price recorded the spooky rap and laugh at the end of Michael Jackson's 'Thriller'."),
 ("mariah carey - fantasy", "Which Wu-Tang Clan rapper was on the remix of 'Fantasy'?", "Ol' Dirty Bastard", ["Method Man", "RZA", "Ghostface Killah"],
  "A pop diva meets Wu-Tang", "The famous remix of Mariah Carey's 'Fantasy' featured Ol' Dirty Bastard of the Wu-Tang Clan."),
 ("whitney houston - it's not right but it's okay", "Which soul legend was Whitney Houston's cousin?", "Dionne Warwick", ["Diana Ross", "Gladys Knight", "Patti LaBelle"],
  "Music runs in the family", "Whitney Houston's mother was gospel singer Cissy Houston, and her cousin was Dionne Warwick."),
 ("justin timberlake - rock your body", "At the 2004 Super Bowl halftime show, Justin Timberlake ended this song in a famous 'wardrobe malfunction' with which singer?", "Janet Jackson", ["Britney Spears", "Beyonce", "Madonna"],
  "The most famous halftime moment", "Justin Timberlake sang 'Rock Your Body' with Janet Jackson at the 2004 Super Bowl halftime show, which ended in the infamous wardrobe malfunction."),
 ("u2 - beautiful day", "What is Bono's real name?", "Paul Hewson", ["David Evans", "Larry Mullen", "Adam Clayton"],
  "Bono's real name", "U2's frontman Bono was born Paul Hewson in Dublin."),
 ("ginuwine - pony", "Which 2012 movie about male dancers famously features 'Pony'?", "Magic Mike", ["The Full Monty", "Zoolander", "Step Up"],
  "A dance-floor comeback", "'Pony' got a huge second life when Channing Tatum danced to it in the 2012 movie 'Magic Mike'."),
 ("dr. dre, snoop dogg - still d.r.e.", "Which headphone company did Dr. Dre co-found and sell to Apple for about $3 billion?", "Beats", ["Bose", "Skullcandy", "Sennheiser"],
  "From rap to billions", "Dr. Dre co-founded Beats Electronics, which Apple bought in 2014 for about $3 billion."),
 ("britney spears - oops", "In the year 'Oops!...I Did It Again' came out (2000), which state's vote count decided the presidential election?", "Florida", ["Ohio", "Texas", "Michigan"],
  "The year of the hanging chads", "In 2000 the Bush-Gore election came down to a recount in Florida, with 'hanging chads' becoming the buzzword of the year."),
 ("kesha - tik tok", "What symbol did Kesha use in place of the 'S' in her name back then?", "A dollar sign", ["An exclamation point", "A star", "A hashtag"],
  "Ke$ha", "When 'TiK ToK' took over the radio, the singer was billed as Ke$ha, with a dollar sign for the S."),
 ("foo fighters - learn to fly", "The name 'Foo Fighters' comes from what in World War II?", "Mysterious lights pilots reported", ["A secret code name", "A fighter squadron", "A German weapon"],
  "Named after UFOs", "'Foo fighters' was the WWII nickname for strange glowing lights that Allied pilots reported, which Dave Grohl borrowed for his band's name."),
 ("whitney houston - i will always love you", "Which 1992 movie, starring Kevin Costner, featured 'I Will Always Love You'?", "The Bodyguard", ["Waiting to Exhale", "Sparkle", "The Preacher's Wife"],
  "From the big screen", "Whitney Houston sang 'I Will Always Love You' in 'The Bodyguard' (1992), and it spent 14 weeks at #1."),
 ("eminem - without me", "Which 2002 movie starring Eminem was loosely based on his life?", "8 Mile", ["Hustle & Flow", "Get Rich or Die Tryin'", "Training Day"],
  "Eminem goes to the movies", "Eminem starred in '8 Mile' in 2002, and its song 'Lose Yourself' won an Oscar."),
 ("sam cooke - chain gang", "Sam Cooke began his career singing in which style?", "Gospel", ["Country", "Jazz", "Opera"],
  "From church to the charts", "Sam Cooke started out in the gospel group The Soul Stirrers before crossing over to pop."),
 ("fugees, ms. lauryn hill - killing me softly", "In which country was Fugees member Wyclef Jean born?", "Haiti", ["Jamaica", "Nigeria", "Cuba"],
  "Fugees roots", "Wyclef Jean was born in Haiti, and the group's name 'Fugees' is short for 'refugees'."),
 ("red hot chili peppers - scar tissue", "What's the nickname of the Chili Peppers' wild bass player?", "Flea", ["Slash", "Duff", "Krist"],
  "A bassist named Flea", "The Chili Peppers' bassist goes by 'Flea' (his real name is Michael Balzary) and is known for his hyperactive playing."),
 ("prince - the most beautiful girl in the world", "In 1993 Prince changed his name to what?", "An unpronounceable symbol", ["The Purple One", "Nelson", "Joey"],
  "The artist formerly known as...", "This was the first big hit Prince released after changing his name to an unpronounceable symbol in 1993."),
 ("rihanna, jay-z - umbrella", "Which rapper joins Rihanna on 'Umbrella'?", "Jay-Z", ["Kanye West", "Eminem", "Nas"],
  "Rihanna and the boss", "Jay-Z, who was then running Def Jam and had signed Rihanna, opens 'Umbrella' with a verse."),
 ("p!nk - so what", "P!nk's husband, Carey Hart, is a pro in which sport?", "Motocross", ["Skateboarding", "Surfing", "Boxing"],
  "Rock star meets motocross", "P!nk's husband Carey Hart is a freestyle motocross rider, and 'So What' came out during their split before they got back together."),
 ("ciara, missy elliott - 1, 2 step", "In 2004, which team broke an 86-year World Series drought?", "Boston Red Sox", ["Chicago Cubs", "Philadelphia Phillies", "New York Mets"],
  "2004 in the news", "The year '1, 2 Step' hit, the Boston Red Sox won their first World Series since 1918."),
 ("paul simon - you can call me al", "Which Saturday Night Live star lip-syncs along in the video for 'You Can Call Me Al'?", "Chevy Chase", ["Bill Murray", "Dan Aykroyd", "Steve Martin"],
  "A very funny video", "Chevy Chase famously clowns alongside Paul Simon in the video for 'You Can Call Me Al'."),
 ("avril lavigne - complicated", "What was Avril Lavigne's debut album called?", "Let Go", ["Under My Skin", "Goodbye Lullaby", "The Best Damn Thing"],
  "The skater-girl breakthrough", "'Complicated' came from Avril Lavigne's 2002 debut album 'Let Go', which sold millions."),
 ("bruce springsteen - glory days", "What is Bruce Springsteen's backing band called?", "The E Street Band", ["The Heartbreakers", "The Attractions", "The Revolution"],
  "The Boss and his band", "Bruce Springsteen plays with the E Street Band, which includes saxophonist Clarence Clemons."),
 ("michael jackson - they don't care about us", "What was the name of Michael Jackson's California ranch?", "Neverland", ["Graceland", "Wonderland", "Sunnydale"],
  "Neverland", "Michael Jackson's home was the Neverland Ranch in Santa Barbara County, which had its own amusement park."),
 ("christina aguilera - come on over baby", "Christina Aguilera got an early start on which Disney TV show?", "The Mickey Mouse Club", ["Lizzie McGuire", "Hannah Montana", "Zoey 101"],
  "A Mouseketeer first", "Christina Aguilera was a cast member of 'The Mickey Mouse Club' alongside Britney Spears and Justin Timberlake."),
 ("jennifer lopez - let's get loud", "Jennifer Lopez started as a 'Fly Girl' dancer on which TV show?", "In Living Color", ["Saturday Night Live", "Soul Train", "MADtv"],
  "Jenny from the dance floor", "Jennifer Lopez was a Fly Girl dancer on the sketch comedy show 'In Living Color' before her singing and acting career."),
 ("michael jackson - beat it", "Which rock guitar legend played the solo on 'Beat It'?", "Eddie Van Halen", ["Slash", "Jimmy Page", "Carlos Santana"],
  "A rock solo on a pop hit", "Eddie Van Halen played the blazing guitar solo on 'Beat It' as a favor, and reportedly didn't take any pay."),
 ("britney spears - (you drive me) crazy", "What was Britney Spears' debut single?", "...Baby One More Time", ["Oops!...I Did It Again", "Toxic", "Lucky"],
  "Where it all started", "Britney Spears' debut single '...Baby One More Time' was a #1 hit in 1999."),
 ("deniece williams - let's hear it for the boy", "In the movie 'Footloose', what is banned in the small town?", "Dancing", ["Driving", "Drinking", "Dating"],
  "A town without dancing", "In 'Footloose' (1984), Kevin Bacon's character fights a town ban on dancing."),
 ("rihanna - don't stop the music", "Which Michael Jackson hit does 'Don't Stop the Music' sample?", "Wanna Be Startin' Somethin'", ["Billie Jean", "Thriller", "Bad"],
  "That 'Mama-say mama-sa' hook", "Rihanna's 'Don't Stop the Music' samples the 'Mama-say mama-sa' chant from Michael Jackson's 'Wanna Be Startin' Somethin''."),
 ("lady gaga - bad romance", "What does Lady Gaga call her devoted fans?", "Little Monsters", ["Beliebers", "Swifties", "The Barbz"],
  "Little Monsters", "Lady Gaga calls her fans her 'Little Monsters', and 'Bad Romance' is one of their anthems."),
 ("sade - smooth operator", "In which country was Sade born?", "Nigeria", ["Jamaica", "England", "Ghana"],
  "Sade's roots", "Sade was born in Nigeria and moved to England as a child before 'Smooth Operator' made her a star."),
 ("michael jackson - dirty diana", "What was the name of Michael Jackson's pet chimpanzee?", "Bubbles", ["Charlie", "Mr. Jingles", "Cheeta"],
  "Bubbles the chimp", "Michael Jackson's pet chimp Bubbles famously went everywhere with him in the late 1980s."),
 ("toni braxton - un-break my heart", "Which U.S. city hosted the 1996 Summer Olympics?", "Atlanta", ["Los Angeles", "Chicago", "Dallas"],
  "1996 in the news", "'Un-Break My Heart' topped the charts in 1996, the year Atlanta hosted the Summer Olympics."),
 ("ms. lauryn hill - doo wop", "Lauryn Hill acted in which 1993 movie with Whoopi Goldberg?", "Sister Act 2", ["Ghost", "Boomerang", "Jumanji"],
  "Before the Fugees broke out", "Lauryn Hill played Rita in 'Sister Act 2: Back in the Habit' (1993) before her music career took off."),
 ("christina aguilera - what a girl wants", "What computer bug had everyone worried at the end of 1999?", "Y2K", ["The Melissa virus", "The ILOVEYOU worm", "Blue screen of death"],
  "The year-end panic of 1999", "'What a Girl Wants' was a hit as the world braced for the Y2K bug, when computers might not handle the year 2000."),
 ("mark ronson, bruno mars - uptown funk", "Which U.S. state did Bruno Mars grow up in?", "Hawaii", ["California", "Florida", "New York"],
  "Bruno's island roots", "Bruno Mars grew up in Honolulu, Hawaii, and started performing there as a kid Elvis impersonator."),
 ("toni braxton - he wasn't man enough", "Which reality show became a summer 2000 sensation with the catchphrase 'The tribe has spoken'?", "Survivor", ["Big Brother", "The Real World", "Fear Factor"],
  "The summer of Survivor", "'He Wasn't Man Enough' came out in 2000, the summer 'Survivor' became a TV phenomenon."),
 ("lady gaga - poker face", "Which Queen song inspired the name 'Lady Gaga'?", "Radio Ga Ga", ["Bohemian Rhapsody", "We Will Rock You", "Killer Queen"],
  "How Gaga got her name", "Lady Gaga took her stage name from Queen's 1984 hit 'Radio Ga Ga'."),
 ("justin bieber, ludacris - baby", "On which website was Justin Bieber discovered?", "YouTube", ["MySpace", "Vine", "TikTok"],
  "Discovered online", "Justin Bieber was discovered by manager Scooter Braun after posting covers on YouTube."),
 ("whitney houston - i have nothing", "Which legendary singer was Whitney Houston's godmother?", "Aretha Franklin", ["Diana Ross", "Gladys Knight", "Tina Turner"],
  "A famous godmother", "Aretha Franklin was Whitney Houston's godmother, and Whitney grew up singing gospel in church."),
 ("green day - basket case", "Green Day's set at which 1994 festival ended in a famous mud fight?", "Woodstock '94", ["Lollapalooza", "Coachella", "Warped Tour"],
  "Mud, and a lot of it", "Green Day's set at Woodstock '94 turned into a legendary mud fight just as 'Basket Case' was blowing up."),
 ("rihanna - pon de replay", "Which hurricane devastated New Orleans in 2005, the year 'Pon de Replay' came out?", "Katrina", ["Rita", "Sandy", "Andrew"],
  "2005 in the news", "'Pon de Replay' was a summer 2005 hit, the year Hurricane Katrina hit the Gulf Coast."),
 ("jennifer lopez - waiting for tonight", "Which Tejano singer did Jennifer Lopez play in a 1997 movie?", "Selena", ["Gloria Estefan", "Celia Cruz", "Shakira"],
  "J.Lo's breakout role", "Jennifer Lopez's breakout role was playing Selena in the 1997 biopic 'Selena'."),
 ("c", "Which Titanic ballad is Celine Dion's biggest hit?", "My Heart Will Go On", ["I'm Alive", "The Power of Love", "Because You Loved Me"],
  "Her signature song", "Celine Dion's 'My Heart Will Go On' from 'Titanic' (1997) became her signature song worldwide."),
 ("ricky martin - livin' la vida loca", "Which Puerto Rican boy band was Ricky Martin in as a teenager?", "Menudo", ["Los Lobos", "New Kids on the Block", "Backstreet Boys"],
  "Before the solo career", "Ricky Martin joined the Puerto Rican boy band Menudo at 12 before going solo and starting the 1999 Latin pop boom."),
]


def _celine(key):
    return key.startswith("c") and "all by myself" in key


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    path = args[0] if args else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "track_cache.json")
    with open(path, encoding="utf-8") as f:
        cache = json.load(f)
    if not dry:
        shutil.copy2(path, path + ".before_grammy_%s" % time.strftime("%Y%m%d_%H%M%S"))
    rng = random.Random(1117)
    changed, missing = 0, []
    for k, qs in cache.items():
        hits = [i for i, q in enumerate(qs) if PAT.search(q.get("question", ""))]
        if not hits:
            continue
        spec = next((r for r in R if (_celine(k) if r[0] == "c" else k.startswith(r[0]))), None)
        if spec is None:
            missing.append(k)
            continue
        _, question, right, wrong, headline, full = spec
        choices = [right] + list(wrong)
        rng.shuffle(choices)
        now = time.time()
        i = hits[0]
        qs[i] = {
            "headline": headline, "full": full, "question": question, "choices": choices,
            "correct_index": choices.index(right), "ts": now, "qid": uuid.uuid4().hex[:12],
            "source": "ai",
            "audit": {"stage": "generated", "by": "Claude", "ts": now,
                      "note": "Replaced a 'how many Grammys' question (2026-11-17).",
                      "deep": False, "original": None,
                      "history": [{"ts": now, "who": "Claude", "action": "generated", "note": ""}]},
        }
        for extra in hits[1:]:   # a song never keeps two Grammy-count questions
            qs[extra]["_dup_grammy"] = True
        changed += 1
    for qs in cache.values():
        qs[:] = [q for q in qs if not q.get("_dup_grammy")]
    print(f"replaced {changed} questions; no replacement written for {len(missing)} song(s): {missing}")
    if not dry:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, path)
        print("wrote", path)


if __name__ == "__main__":
    main()
