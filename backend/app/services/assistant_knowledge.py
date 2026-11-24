"""
Knowledge base for the staff help assistant.

Every statement here must match what the backend actually enforces (app/auth/permissions.py, docs/WORKFLOWS.md,
docs/api/SCREEN_TO_API_MAPPING.md). When a workflow, permission, or default changes, update the article in the
same change. The assistant answers only from these articles; it never sees case, person, or contact data.

Keywords are matched against the lower-cased question. ASCII keywords match at the start of a word, so a stem
such as "regist" covers register, registered, and registration. Devanagari keywords match anywhere.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Article:
    id: str
    title: dict[str, str]
    answer: dict[str, str]
    keywords: dict[str, int]


def _a(id: str, title_en: str, title_hi: str, en: str, hi: str, keywords: dict[str, int]) -> Article:
    return Article(id=id, title={"en": title_en, "hi": title_hi}, answer={"en": en, "hi": hi}, keywords=keywords)


ARTICLES: tuple[Article, ...] = (
    _a(
        "register_missing_person", "Missing Person Registration", "गुमशुदा व्यक्ति पंजीकरण",
        "Open Missing Person Registration from Cases, fill in what is known, then submit. No field is mandatory for "
        "a person, but add as much as you can: name, approximate age or age group, gender, clothing, identifying "
        "marks, language, last-seen place and time, and any medical needs or disability. Choose the place from the "
        "location list or capture GPS; administrators must also choose the station. After saving you get a case "
        "number (KSLF-MP-…) and any possible matches, and the case appears under Cases. Add photos or voice notes "
        "from the case page.",
        "Cases में Missing Person Registration खोलें, जो जानकारी उपलब्ध है वह भरें और सबमिट करें। व्यक्ति के लिए कोई "
        "फ़ील्ड अनिवार्य नहीं है, फिर भी जितना हो सके जोड़ें: नाम, अनुमानित आयु या आयु वर्ग, लिंग, कपड़े, पहचान चिह्न, "
        "भाषा, अंतिम बार देखे जाने का स्थान और समय, तथा कोई चिकित्सा आवश्यकता या दिव्यांगता। स्थान सूची से जगह चुनें या "
        "GPS लें; प्रशासक को स्टेशन भी चुनना होता है। सेव करने पर केस नंबर (KSLF-MP-…) और संभावित मिलान दिखते हैं, और "
        "केस Cases में दिखाई देता है। फोटो या वॉयस नोट केस पेज से जोड़ें।",
        {"missing person": 4, "missing child": 4, "missing": 2, "regist": 1, "report a missing": 3,
         "लापता": 3, "गुमशुदा": 3, "गुम ": 2, "दर्ज": 1, "पंजीकरण": 1, "रजिस्टर": 1},
    ),
    _a(
        "register_found_person", "Found Person Registration", "मिले हुए व्यक्ति का पंजीकरण",
        "Open Found Person Registration, record where the person was found and where they are being cared for, "
        "their communication status and any assistance needs, then submit. Mark 'safeguarding review required' when "
        "the person needs a safeguarding review; such a case can later be resolved only with written verification "
        "evidence. After saving (KSLF-FP-…) the system suggests missing-person cases that may match.",
        "Found Person Registration खोलें, व्यक्ति कहाँ मिला और अभी कहाँ देखभाल में है, उसकी संवाद स्थिति और सहायता की "
        "ज़रूरतें दर्ज करें, फिर सबमिट करें। अगर व्यक्ति को सुरक्षा समीक्षा चाहिए तो 'safeguarding review required' चुनें; "
        "ऐसा केस बाद में केवल लिखित सत्यापन साक्ष्य के साथ ही हल (resolve) किया जा सकता है। सेव करने पर (KSLF-FP-…) "
        "सिस्टम संभावित मिलते-जुलते गुमशुदा व्यक्ति केस सुझाता है।",
        {"found person": 4, "found child": 4, "found someone": 3, "found a person": 4, "safeguard": 3,
         "मिला व्यक्ति": 4, "मिले व्यक्ति": 4, "मिले हुए व्यक्ति": 4, "पाया गया व्यक्ति": 4, "मिला बच्चा": 4,
         "मिले हुए बच्चे": 4},
    ),
    _a(
        "register_lost_item", "Lost Item Registration", "खोई वस्तु पंजीकरण",
        "Open Lost Item Registration. Item category and item name are required; also add a description, brand or "
        "colour, identifying features and the last-seen place. Owner contact details are stored encrypted and are not "
        "shown on the case. After saving (KSLF-LI-…) the system suggests found items that may match.",
        "Lost Item Registration खोलें। वस्तु की श्रेणी और नाम अनिवार्य हैं; विवरण, ब्रांड या रंग, पहचान की विशेषताएँ और "
        "अंतिम बार देखे जाने का स्थान भी जोड़ें। मालिक के संपर्क विवरण एन्क्रिप्ट करके रखे जाते हैं और केस पर नहीं दिखते। "
        "सेव करने पर (KSLF-LI-…) सिस्टम संभावित मिलती-जुलती मिली हुई वस्तुएँ सुझाता है।",
        {"lost item": 4, "lost bag": 4, "lost phone": 4, "lost property": 3, "lost": 2,
         "खोई वस्तु": 4, "खोया सामान": 4, "खोई हुई": 3, "खोया": 2, "खोई": 2},
    ),
    _a(
        "register_found_item", "Found Item Registration", "मिली वस्तु पंजीकरण",
        "Open Found Item Registration. Item category and item name are required; record its condition, where it was "
        "found and where it is stored. A found item starts in custody (IN_CUSTODY). Police officers and "
        "administrators record transfers and the final release to the owner from the case page.",
        "Found Item Registration खोलें। वस्तु की श्रेणी और नाम अनिवार्य हैं; उसकी हालत, वह कहाँ मिली और कहाँ रखी गई है, "
        "यह दर्ज करें। मिली वस्तु शुरू से अभिरक्षा (IN_CUSTODY) में रहती है। स्थानांतरण और मालिक को अंतिम सुपुर्दगी पुलिस "
        "अधिकारी और प्रशासक केस पेज से दर्ज करते हैं।",
        {"found item": 4, "found bag": 4, "found phone": 4, "found an item": 4, "found property": 3,
         "मिली वस्तु": 4, "मिला सामान": 4, "मिली हुई वस्तु": 4, "पाया गया सामान": 4},
    ),
    _a(
        "attachments", "Photos and voice notes", "फोटो और वॉयस नोट",
        "Save the case first, then open it and add photos or voice notes. Each file is checked for malware, so it "
        "shows as scanning and can be viewed only after it passes. Only police officers and administrators can "
        "delete an attachment, and they must give a reason.",
        "पहले केस सेव करें, फिर उसे खोलकर फोटो या वॉयस नोट जोड़ें। हर फ़ाइल की मैलवेयर जाँच होती है, इसलिए वह पहले "
        "scanning दिखाती है और जाँच पास होने के बाद ही देखी जा सकती है। अटैचमेंट केवल पुलिस अधिकारी और प्रशासक कारण "
        "बताकर हटा सकते हैं।",
        {"photo": 3, "picture": 3, "image": 2, "voice": 3, "attach": 3, "upload": 3, "scanning": 3,
         "फोटो": 3, "तस्वीर": 3, "वॉयस": 3, "अटैच": 3, "अपलोड": 3},
    ),
    _a(
        "case_search", "Finding a case", "केस खोजना",
        "Open Cases to see the list for each case type. Filter by status, urgency, dates, assignee and more, or "
        "search by text or case number. Lists show only cases within your station's scope. Police officers and "
        "administrators can search across all stations; results outside your scope are summaries that you cannot "
        "open. The map and nearby views show cases by location.",
        "हर प्रकार के केस की सूची देखने के लिए Cases खोलें। स्थिति, तात्कालिकता, तारीख, असाइनी आदि से फ़िल्टर करें, या "
        "टेक्स्ट अथवा केस नंबर से खोजें। सूची में केवल आपके स्टेशन के दायरे के केस दिखते हैं। पुलिस अधिकारी और प्रशासक सभी "
        "स्टेशनों में खोज सकते हैं; आपके दायरे से बाहर के परिणाम केवल सारांश होते हैं जिन्हें खोला नहीं जा सकता। मानचित्र "
        "और आसपास (nearby) दृश्य केस को स्थान के अनुसार दिखाते हैं।",
        {"search": 3, "find a case": 4, "find case": 4, "filter": 3, "case number": 2, "map": 2, "nearby": 3,
         "खोज": 3, "ढूंढ": 3, "ढूँढ": 3, "फ़िल्टर": 3, "फिल्टर": 3, "केस नंबर": 2, "मानचित्र": 2, "नक्शा": 2},
    ),
    _a(
        "case_status", "Case status and tracking", "केस की स्थिति और ट्रैकिंग",
        "A case moves through OPEN, UNDER_REVIEW, ASSIGNED, MATCH_FOUND, RESOLVED, CLOSED and REOPENED, and every "
        "change needs a reason. Any staff member can mark a case Under Review; closing, resolving and reopening need "
        "a police officer or administrator. Resolving needs a confirmed match or written verification evidence, and a "
        "found item must be released first. Open the case history to track every change. If you see 'case changed', "
        "reload the case and try again.",
        "केस की स्थितियाँ OPEN, UNDER_REVIEW, ASSIGNED, MATCH_FOUND, RESOLVED, CLOSED और REOPENED हैं, और हर बदलाव के "
        "लिए कारण देना होता है। कोई भी स्टाफ़ सदस्य केस को Under Review कर सकता है; बंद करना, हल करना और दोबारा खोलना "
        "पुलिस अधिकारी या प्रशासक ही कर सकते हैं। हल करने के लिए पुष्ट मिलान या लिखित सत्यापन साक्ष्य चाहिए, और मिली वस्तु "
        "पहले सुपुर्द (RELEASED) होनी चाहिए। हर बदलाव देखने के लिए केस का इतिहास (history) खोलें। 'case changed' दिखे तो "
        "केस दोबारा लोड करके फिर कोशिश करें।",
        {"status": 3, "track": 3, "close": 2, "resolve": 2, "reopen": 3, "under review": 3, "history": 2,
         "case changed": 4, "स्थिति": 3, "स्टेटस": 3, "ट्रैक": 3, "बंद": 2, "दोबारा खोल": 3, "इतिहास": 2},
    ),
    _a(
        "case_assignment", "Assigning a case", "केस असाइन करना",
        "Police officers and administrators assign a case to a police officer at the case's station from the case "
        "page, optionally with a reason. When supervisor approval is switched on, the assignment becomes a request "
        "that an administrator approves or rejects. Help desk operators cannot assign cases.",
        "पुलिस अधिकारी और प्रशासक केस पेज से केस को उसी स्टेशन के किसी पुलिस अधिकारी को असाइन करते हैं, चाहें तो कारण "
        "के साथ। जब पर्यवेक्षक अनुमोदन चालू हो, तो असाइनमेंट एक अनुरोध बन जाता है जिसे प्रशासक स्वीकार या अस्वीकार करता है। "
        "हेल्प डेस्क ऑपरेटर केस असाइन नहीं कर सकते।",
        {"assign": 4, "reassign": 4, "allocate": 3, "approval request": 3,
         "असाइन": 4, "सौंपना": 2, "आवंटित": 3},
    ),
    _a(
        "case_notes", "Case notes", "केस नोट",
        "Open the case and add a note (operational or follow-up). Notes cannot be edited or deleted; to fix one, add "
        "a correction note that refers to it. Police officers and administrators can mark a note as sensitive, and "
        "help desk operators do not see sensitive notes.",
        "केस खोलें और नोट जोड़ें (operational या follow-up)। नोट संपादित या हटाए नहीं जा सकते; गलती सुधारने के लिए उस नोट "
        "का संदर्भ देते हुए correction नोट जोड़ें। पुलिस अधिकारी और प्रशासक किसी नोट को संवेदनशील (sensitive) चिह्नित कर "
        "सकते हैं, और हेल्प डेस्क ऑपरेटर संवेदनशील नोट नहीं देख सकते।",
        {"note": 4, "comment on a case": 3, "correction": 3, "नोट": 4, "टिप्पणी": 3},
    ),
    _a(
        "contact_reveal", "Viewing reporter contact details", "रिपोर्ट करने वाले का संपर्क विवरण देखना",
        "Reporter contact details are encrypted and hidden on the case. A police officer or administrator can reveal "
        "them from the case page by choosing a reason, and every reveal is recorded in the audit log. Help desk "
        "operators cannot reveal contact details.",
        "रिपोर्ट करने वाले के संपर्क विवरण एन्क्रिप्टेड रहते हैं और केस पर छिपे रहते हैं। पुलिस अधिकारी या प्रशासक केस पेज "
        "से कारण चुनकर उन्हें देख सकते हैं, और हर बार देखना ऑडिट लॉग में दर्ज होता है। हेल्प डेस्क ऑपरेटर संपर्क विवरण नहीं "
        "देख सकते।",
        {"contact": 3, "phone number": 3, "mobile number": 3, "reveal": 4, "family number": 3,
         "संपर्क": 3, "फ़ोन नंबर": 3, "फोन नंबर": 3, "मोबाइल नंबर": 3},
    ),
    _a(
        "matching", "Matching and match review", "मिलान और मिलान समीक्षा",
        "After a case is saved or edited the system suggests possible matches: persons with persons, items with "
        "items. A suggestion is not proof of identity. Any staff member can view suggestions or propose a match with "
        "a written rationale; only a police officer or administrator can confirm or reject it with evidence, and you "
        "cannot confirm a match you proposed. Confirming moves both cases to MATCH_FOUND. Open Matches for the review "
        "queue.",
        "केस सेव या संपादित होने के बाद सिस्टम संभावित मिलान सुझाता है: व्यक्ति का व्यक्ति से, वस्तु का वस्तु से। सुझाव "
        "पहचान का प्रमाण नहीं है। कोई भी स्टाफ़ सदस्य सुझाव देख सकता है या लिखित कारण के साथ मिलान प्रस्तावित कर सकता है; "
        "साक्ष्य के साथ पुष्टि या अस्वीकार केवल पुलिस अधिकारी या प्रशासक करते हैं, और आप अपना प्रस्तावित मिलान खुद पुष्ट नहीं "
        "कर सकते। पुष्टि होने पर दोनों केस MATCH_FOUND हो जाते हैं। समीक्षा कतार के लिए Matches खोलें।",
        {"match": 4, "suggestion": 2, "candidate": 2, "confirm": 2, "मिलान": 4, "मैच": 4, "सुझाव": 2},
    ),
    _a(
        "person_reunification", "Person reunification", "व्यक्ति पुनर्मिलन",
        "First a police officer or administrator confirms the match. They then open a handover on the case: record "
        "the claimant (and an optional witness), verify the claimant's identity with an approved method, and "
        "complete the handover with the place, time and acknowledgement. Then resolve both cases. A rejected "
        "handover can be followed by a new one. Nothing is marked reunited automatically.",
        "पहले पुलिस अधिकारी या प्रशासक मिलान की पुष्टि करते हैं। फिर वे केस पर हैंडओवर खोलते हैं: दावेदार (और चाहें तो "
        "गवाह) का विवरण दर्ज करते हैं, स्वीकृत तरीके से दावेदार की पहचान सत्यापित करते हैं, और स्थान, समय व पावती के साथ "
        "हैंडओवर पूरा करते हैं। इसके बाद दोनों केस हल (resolve) करें। अस्वीकृत हैंडओवर के बाद नया हैंडओवर खोला जा सकता है। "
        "कुछ भी अपने आप पुनर्मिलित नहीं माना जाता।",
        {"reunit": 4, "reunion": 4, "handover": 3, "hand over": 3, "claimant": 3, "family": 1, "witness": 2,
         "पुनर्मिलन": 4, "मिलाना": 3, "परिवार से मिला": 4, "हैंडओवर": 3, "दावेदार": 3},
    ),
    _a(
        "item_reunification", "Item return and custody", "वस्तु वापसी और अभिरक्षा",
        "Confirm the match between the lost item and the found item. A police officer or administrator then records "
        "the handover from the found-item case and releases custody with the recipient's acknowledgement. The "
        "found-item case can be resolved only after custody is RELEASED. Custody can also be transferred to another "
        "storage location (the location is required); RELEASED is final.",
        "खोई और मिली वस्तु के बीच मिलान की पुष्टि करें। फिर पुलिस अधिकारी या प्रशासक मिली वस्तु वाले केस से हैंडओवर दर्ज "
        "करते हैं और प्राप्तकर्ता की पावती के साथ अभिरक्षा सुपुर्द (RELEASED) करते हैं। अभिरक्षा RELEASED होने के बाद ही मिली "
        "वस्तु का केस हल किया जा सकता है। अभिरक्षा किसी दूसरे भंडारण स्थान पर स्थानांतरित भी की जा सकती है (स्थान बताना "
        "अनिवार्य है); RELEASED अंतिम स्थिति है।",
        {"custody": 4, "return": 3, "give back": 3, "hand back": 3, "release": 3, "storage": 2, "owner": 2,
         "अभिरक्षा": 4, "कस्टडी": 4, "वापस": 3, "लौटा": 3, "सुपुर्द": 3, "मालिक को": 3},
    ),
    _a(
        "public_submissions", "Citizen submissions", "नागरिकों की रिपोर्ट",
        "Citizens can report through the public portal after verifying their mobile number with an OTP. Their reports "
        "arrive as cases awaiting staff verification. Open the case and choose Verify or Reject with a reason.",
        "नागरिक OTP से अपना मोबाइल नंबर सत्यापित करने के बाद सार्वजनिक पोर्टल से रिपोर्ट कर सकते हैं। उनकी रिपोर्ट स्टाफ़ "
        "सत्यापन की प्रतीक्षा वाले केस के रूप में आती हैं। केस खोलें और कारण के साथ Verify या Reject चुनें।",
        {"citizen": 3, "public portal": 4, "public submission": 4, "pilgrim report": 3, "verify submission": 4,
         "नागरिक": 3, "पोर्टल": 3, "श्रद्धालु": 2},
    ),
    _a(
        "family_notifications", "Family SMS and WhatsApp messages", "परिवार को SMS और WhatsApp संदेश",
        "From the case page you can queue an SMS or WhatsApp message to the family using an approved template, in "
        "Hindi or English, after recording their consent. The message shows PENDING, SENT or FAILED. It is delivered "
        "only if a messaging provider is set up for this deployment, so do not tell the family it was sent until it "
        "shows SENT.",
        "केस पेज से आप परिवार की सहमति दर्ज करने के बाद स्वीकृत टेम्पलेट से हिंदी या अंग्रेज़ी में SMS या WhatsApp संदेश "
        "कतार में डाल सकते हैं। संदेश की स्थिति PENDING, SENT या FAILED दिखती है। संदेश तभी पहुँचता है जब इस डिप्लॉयमेंट में "
        "मैसेजिंग प्रदाता सेट हो, इसलिए SENT दिखने से पहले परिवार को न बताएं कि संदेश भेज दिया गया है।",
        {"sms": 4, "whatsapp": 4, "message the family": 4, "notify the family": 4, "inform family": 4,
         "एसएमएस": 4, "व्हाट्सएप": 4, "संदेश": 3, "परिवार को सूचित": 4},
    ),
    _a(
        "role_police", "Police officer operations", "पुलिस अधिकारी के कार्य",
        "Police officers can do everything a help desk operator can, and also assign cases; close, resolve and "
        "reopen them; confirm or reject matches; update found-item custody; record handovers; reveal contact details "
        "with a reason; add sensitive notes; delete attachments; search across stations; and generate reports.",
        "पुलिस अधिकारी हेल्प डेस्क ऑपरेटर के सभी काम कर सकते हैं, और साथ ही केस असाइन करना; केस बंद, हल और दोबारा खोलना; "
        "मिलान की पुष्टि या अस्वीकार करना; मिली वस्तु की अभिरक्षा अपडेट करना; हैंडओवर दर्ज करना; कारण बताकर संपर्क विवरण "
        "देखना; संवेदनशील नोट जोड़ना; अटैचमेंट हटाना; सभी स्टेशनों में खोजना; और रिपोर्ट बनाना भी कर सकते हैं।",
        {"police": 3, "officer": 3, "पुलिस": 3, "अधिकारी": 2},
    ),
    _a(
        "role_help_desk", "Help desk operations", "हेल्प डेस्क के कार्य",
        "Help desk operators register all four case types, update cases, mark them Under Review, add notes, upload "
        "photos and voice notes, propose matches, verify citizen submissions, queue family messages, handle citizen "
        "complaints, and reply to citizen chats handed over by the chatbot. They see their own station's cases.",
        "हेल्प डेस्क ऑपरेटर चारों प्रकार के केस दर्ज करते हैं, केस अपडेट करते हैं, उन्हें Under Review करते हैं, नोट जोड़ते "
        "हैं, फोटो और वॉयस नोट अपलोड करते हैं, मिलान प्रस्तावित करते हैं, नागरिकों की रिपोर्ट सत्यापित करते हैं, परिवार के "
        "संदेश कतार में डालते हैं, नागरिक शिकायतें संभालते हैं, और चैटबॉट से सौंपी गई नागरिक चैट का जवाब देते हैं। उन्हें अपने "
        "स्टेशन के केस दिखते हैं।",
        {"help desk": 4, "helpdesk": 4, "operator": 3, "हेल्प डेस्क": 4, "ऑपरेटर": 3},
    ),
    _a(
        "role_volunteer", "Volunteers", "स्वयंसेवक",
        "Kumbh Milan has three staff roles: Administrator, Police Officer and Help Desk Operator. There is no separate "
        "volunteer role. If you volunteer at a help desk, ask your administrator for an account; what you can do "
        "depends on the role they assign.",
        "Kumbh Milan में तीन स्टाफ़ भूमिकाएँ हैं: प्रशासक, पुलिस अधिकारी और हेल्प डेस्क ऑपरेटर। स्वयंसेवक के लिए अलग भूमिका "
        "नहीं है। अगर आप हेल्प डेस्क पर स्वयंसेवा करते हैं, तो अपने प्रशासक से खाता माँगें; आप क्या कर सकते हैं यह उनकी दी "
        "गई भूमिका पर निर्भर करता है।",
        {"volunteer": 5, "role": 2, "स्वयंसेवक": 5, "वॉलंटियर": 5, "वालंटियर": 5, "भूमिका": 2},
    ),
    _a(
        "role_admin", "Administrator operations", "प्रशासक के कार्य",
        "Administrators have all police officer permissions and also manage users, stations and locations, approve "
        "assignment requests and public alerts, publish public information, schedule reports, manage retention and "
        "legal holds, read the audit log, and configure integrations.",
        "प्रशासक के पास पुलिस अधिकारी की सभी अनुमतियाँ होती हैं, और वे उपयोगकर्ता, स्टेशन और स्थान भी प्रबंधित करते हैं, "
        "असाइनमेंट अनुरोध और सार्वजनिक अलर्ट स्वीकृत करते हैं, सार्वजनिक जानकारी प्रकाशित करते हैं, रिपोर्ट शेड्यूल करते हैं, "
        "डेटा प्रतिधारण और लीगल होल्ड संभालते हैं, ऑडिट लॉग पढ़ते हैं, और इंटीग्रेशन कॉन्फ़िगर करते हैं।",
        {"admin": 3, "new user": 3, "create user": 4, "add user": 4, "station": 1, "audit": 3, "retention": 3,
         "प्रशासक": 3, "एडमिन": 3, "नया उपयोगकर्ता": 4, "ऑडिट": 3},
    ),
    _a(
        "login", "Signing in", "साइन इन करना",
        "Sign in with your username and password. By default, 5 failed attempts within 15 minutes lock the account "
        "for a while; wait and try again, or contact your administrator. If your account uses two-step sign-in, you "
        "then enter the 6-digit code from your authenticator app. You stay signed in while the app renews your "
        "session in the background.",
        "अपने यूज़रनेम और पासवर्ड से साइन इन करें। डिफ़ॉल्ट रूप से 15 मिनट में 5 बार गलत प्रयास होने पर खाता कुछ समय के "
        "लिए लॉक हो जाता है; थोड़ी देर बाद फिर कोशिश करें या अपने प्रशासक से संपर्क करें। अगर आपके खाते में दो-चरणीय साइन इन "
        "है, तो इसके बाद ऑथेंटिकेटर ऐप का 6 अंकों का कोड डालें। ऐप पृष्ठभूमि में आपका सत्र नवीनीकृत करता रहता है, इसलिए आप "
        "साइन इन बने रहते हैं।",
        {"log in": 4, "login": 4, "sign in": 4, "signin": 4, "locked": 4, "lockout": 4, "username": 2,
         "लॉगिन": 4, "लॉग इन": 4, "साइन इन": 4, "लॉक": 4, "यूज़रनेम": 2},
    ),
    _a(
        "password", "Changing or resetting your password", "पासवर्ड बदलना या रीसेट करना",
        "To change your password, open your profile and choose Change password. The new password needs at least 12 "
        "characters, three of lowercase, uppercase, digits and symbols, and must not contain your username. Changing "
        "it signs you out everywhere. If you forgot it, use Forgot password on the sign-in screen to get a one-time "
        "code by email or SMS (when your account has one and delivery is set up), or ask an administrator to reset "
        "it.",
        "पासवर्ड बदलने के लिए अपनी प्रोफ़ाइल खोलें और Change password चुनें। नया पासवर्ड कम से कम 12 अक्षरों का हो, उसमें "
        "छोटे अक्षर, बड़े अक्षर, अंक और चिह्न में से कम से कम तीन प्रकार हों, और उसमें आपका यूज़रनेम न हो। पासवर्ड बदलते ही "
        "आप हर जगह से साइन आउट हो जाते हैं। पासवर्ड भूल गए हों तो साइन इन स्क्रीन पर Forgot password से ईमेल या SMS पर "
        "एक बार का कोड लें (अगर खाते में ईमेल या मोबाइल दर्ज है और डिलीवरी सेट है), या प्रशासक से पासवर्ड रीसेट करवाएं।",
        {"password": 4, "forgot": 3, "reset": 2, "पासवर्ड": 4, "भूल": 3},
    ),
    _a(
        "mfa_setup", "Setting up two-step sign-in (MFA)", "दो-चरणीय साइन इन (MFA) सेट करना",
        "Open your profile and choose to set up two-step sign-in. Add the secret it shows to an authenticator app, "
        "then type the current 6-digit code to confirm. From then on every sign-in asks for a code, and your other "
        "signed-in devices are signed out. If you see 'MFA is disabled', two-step sign-in is not switched on for this "
        "deployment; ask your administrator.",
        "अपनी प्रोफ़ाइल खोलें और दो-चरणीय साइन इन सेट करना चुनें। दिखाया गया सीक्रेट किसी ऑथेंटिकेटर ऐप में जोड़ें, फिर "
        "पुष्टि के लिए वर्तमान 6 अंकों का कोड डालें। इसके बाद हर साइन इन पर कोड माँगा जाएगा, और आपके दूसरे साइन-इन डिवाइस "
        "साइन आउट हो जाएँगे। अगर 'MFA is disabled' दिखे, तो इस डिप्लॉयमेंट में दो-चरणीय साइन इन चालू नहीं है; अपने प्रशासक "
        "से पूछें।",
        {"mfa": 4, "two-step": 4, "two step": 4, "two-factor": 4, "2fa": 4, "authenticator": 3, "otp app": 3,
         "set up": 1, "enable": 1, "एमएफए": 4, "दो-चरण": 4, "दो चरण": 4, "ऑथेंटिकेटर": 3, "सेट": 1},
    ),
    _a(
        "mfa_login", "Signing in with an authenticator code", "ऑथेंटिकेटर कोड से साइन इन",
        "After your password, enter the current 6-digit code from your authenticator app within 5 minutes. You can "
        "retry a wrong code a few times; too many wrong codes end the attempt and count toward the account lock, so "
        "sign in again. Each code works only once. If you lost the device with your authenticator app, contact your "
        "administrator.",
        "पासवर्ड के बाद 5 मिनट के भीतर ऑथेंटिकेटर ऐप का वर्तमान 6 अंकों का कोड डालें। गलत कोड कुछ बार दोबारा डाला जा "
        "सकता है; बहुत बार गलत कोड डालने पर प्रयास समाप्त हो जाता है और वह खाता लॉक की गिनती में जुड़ता है, तब फिर से साइन "
        "इन करें। हर कोड केवल एक बार काम करता है। अगर ऑथेंटिकेटर ऐप वाला डिवाइस खो गया है, तो अपने प्रशासक से संपर्क करें।",
        {"6-digit": 3, "six digit": 3, "authenticator code": 5, "verification code": 3, "code is wrong": 4,
         "wrong code": 4, "lost my phone": 4, "lost my authenticator": 6, "new phone": 3,
         "कोड": 2, "6 अंक": 3, "गलत कोड": 4, "फ़ोन खो": 4, "फोन खो": 4},
    ),
    _a(
        "navigation", "Finding your way around", "ऐप में नेविगेशन",
        "The main areas are: Dashboard for counts; Cases, with lists for missing persons, found persons, lost items "
        "and found items, plus search and map; each case page with details, history, notes, attachments, matches, "
        "handovers and family messages; Matches for the review queue; Notifications for your alerts; Reports; and, "
        "for administrators, users, stations, locations, audit and retention. Menus show only what your role allows.",
        "मुख्य हिस्से हैं: गिनती के लिए Dashboard; Cases, जिसमें गुमशुदा व्यक्ति, मिले व्यक्ति, खोई वस्तु और मिली वस्तु की "
        "सूचियाँ तथा खोज और मानचित्र हैं; हर केस पेज, जिसमें विवरण, इतिहास, नोट, अटैचमेंट, मिलान, हैंडओवर और परिवार के संदेश "
        "हैं; समीक्षा कतार के लिए Matches; आपकी सूचनाओं के लिए Notifications; Reports; और प्रशासकों के लिए उपयोगकर्ता, "
        "स्टेशन, स्थान, ऑडिट और प्रतिधारण। मेनू में केवल वही दिखता है जिसकी आपकी भूमिका को अनुमति है।",
        {"navigat": 4, "menu": 4, "where is": 2, "where do i find": 3, "screen": 2, "home page": 3,
         "मेनू": 4, "कहाँ मिलेगा": 3, "कहां मिलेगा": 3, "स्क्रीन": 2, "नेविगेशन": 4},
    ),
    _a(
        "dashboard_reports", "Dashboard and reports", "डैशबोर्ड और रिपोर्ट",
        "The dashboard shows counts for the cases you can see, such as urgent and unassigned active cases, cases "
        "registered today and pending match reviews, for today, 7 days or 30 days. Staff can export a CSV of cases by "
        "creation date. Police officers and administrators can generate CSV or PDF reports, and administrators can "
        "schedule them.",
        "डैशबोर्ड उन केसों की गिनती दिखाता है जिन्हें आप देख सकते हैं, जैसे तात्कालिक और बिना असाइन सक्रिय केस, आज दर्ज केस "
        "और लंबित मिलान समीक्षाएँ, आज, 7 दिन या 30 दिन के लिए। स्टाफ़ बनने की तारीख के अनुसार केसों का CSV निर्यात कर सकता "
        "है। पुलिस अधिकारी और प्रशासक CSV या PDF रिपोर्ट बना सकते हैं, और प्रशासक उन्हें शेड्यूल कर सकते हैं।",
        {"dashboard": 4, "report": 2, "export": 3, "csv": 4, "pdf": 4, "statistic": 3, "count": 2,
         "डैशबोर्ड": 4, "निर्यात": 3, "आंकड़े": 3, "आँकड़े": 3},
    ),
    _a(
        "grievances", "Citizen complaints (grievances)", "नागरिक शिकायतें",
        "Citizens file complaints and get a reference number. Help desk operators, police officers and administrators "
        "see the grievance list, assign grievances, update their status (OPEN, ASSIGNED, IN_PROGRESS, RESOLVED, "
        "CLOSED, REOPENED) and add comments. By default a grievance should be handled within 72 hours and escalates "
        "automatically when overdue.",
        "नागरिक शिकायत दर्ज करते हैं और उन्हें संदर्भ संख्या मिलती है। हेल्प डेस्क ऑपरेटर, पुलिस अधिकारी और प्रशासक शिकायतों "
        "की सूची देखते हैं, उन्हें असाइन करते हैं, उनकी स्थिति (OPEN, ASSIGNED, IN_PROGRESS, RESOLVED, CLOSED, REOPENED) "
        "अपडेट करते हैं और टिप्पणियाँ जोड़ते हैं। डिफ़ॉल्ट रूप से शिकायत 72 घंटे में निपटानी होती है और समय पर न निपटने पर "
        "अपने आप एस्केलेट हो जाती है।",
        {"grievance": 5, "complaint": 5, "complain": 4, "शिकायत": 5},
    ),
    _a(
        "chat_handoff", "Citizen chats handed to staff", "स्टाफ़ को सौंपी गई नागरिक चैट",
        "When a citizen using the chatbot asks for a person or reports an emergency, the chat is handed to staff. "
        "Help desk operators, police officers and administrators see waiting chats in the chat queue, can reply, and "
        "close the chat when done.",
        "जब चैटबॉट इस्तेमाल करने वाला नागरिक किसी व्यक्ति से बात करना चाहता है या आपात स्थिति बताता है, तो चैट स्टाफ़ को "
        "सौंप दी जाती है। हेल्प डेस्क ऑपरेटर, पुलिस अधिकारी और प्रशासक चैट कतार में प्रतीक्षारत चैट देखते हैं, जवाब दे सकते "
        "हैं, और काम पूरा होने पर चैट बंद कर सकते हैं।",
        {"chat": 4, "chatbot": 4, "handoff": 4, "handed off": 4, "चैट": 4, "चैटबॉट": 4},
    ),
    _a(
        "offline_sync", "Working offline on mobile", "मोबाइल पर ऑफ़लाइन काम",
        "The mobile app can save registrations while offline and uploads them when the connection returns; retries do "
        "not create duplicate cases. Photos taken offline upload in resumable parts.",
        "मोबाइल ऐप ऑफ़लाइन रहते हुए पंजीकरण सेव कर सकता है और कनेक्शन लौटने पर उन्हें अपलोड करता है; दोबारा कोशिश करने से "
        "डुप्लिकेट केस नहीं बनते। ऑफ़लाइन ली गई फोटो रुक-रुक कर भी (resumable) अपलोड होती हैं।",
        {"offline": 5, "no internet": 5, "no network": 5, "sync": 4, "ऑफ़लाइन": 5, "ऑफलाइन": 5, "इंटरनेट नहीं": 5,
         "नेटवर्क नहीं": 5, "सिंक": 4},
    ),
    _a(
        "staff_notifications", "Your notifications", "आपकी सूचनाएँ",
        "The Notifications area lists alerts for you, such as new match candidates, cases assigned to you, assignment "
        "approvals and decisions, grievances assigned or escalated, reports ready to download, and chats handed to "
        "staff. Open an alert to go to the item and mark it as read.",
        "Notifications में आपके लिए अलर्ट दिखते हैं, जैसे नए संभावित मिलान, आपको असाइन किए गए केस, असाइनमेंट अनुमोदन और "
        "निर्णय, असाइन या एस्केलेट हुई शिकायतें, डाउनलोड के लिए तैयार रिपोर्ट, और स्टाफ़ को सौंपी गई चैट। अलर्ट खोलकर संबंधित "
        "आइटम पर जाएँ और उसे पढ़ा हुआ चिह्नित करें।",
        {"notification": 4, "alert": 2, "unread": 4, "bell": 3, "सूचना": 4, "नोटिफिकेशन": 4, "अलर्ट": 2},
    ),
    _a(
        "about_assistant", "What the assistant can help with", "सहायक किन बातों में मदद कर सकता है",
        "I answer questions about using Kumbh Milan: registering missing and found persons and lost and found items, "
        "finding and updating cases, case status, matching, reunification and custody, family messages, staff roles, "
        "signing in, passwords and two-step sign-in. I cannot look up cases or personal details.",
        "मैं Kumbh Milan के उपयोग से जुड़े सवालों के जवाब देता हूँ: गुमशुदा और मिले व्यक्तियों तथा खोई और मिली वस्तुओं का "
        "पंजीकरण, केस खोजना और अपडेट करना, केस की स्थिति, मिलान, पुनर्मिलन और अभिरक्षा, परिवार को संदेश, स्टाफ़ भूमिकाएँ, साइन "
        "इन, पासवर्ड और दो-चरणीय साइन इन। मैं केस या व्यक्तिगत विवरण नहीं देख सकता।",
        {"what can you": 5, "how can you help": 5, "what do you do": 5, "hello": 2, "namaste": 2, "help me": 1,
         "क्या कर सकते": 5, "कैसे मदद": 4, "नमस्ते": 2, "मदद": 1},
    ),
)

CASE_LOOKUP = _a(
    "case_lookup", "Finding a case", "केस खोजना",
    "I can't look up individual cases, people or contact details. Open Cases and search by the case number; you will "
    "see the case only if it is within your scope.",
    "मैं किसी केस, व्यक्ति या संपर्क विवरण को नहीं देख सकता। Cases खोलें और केस नंबर से खोजें; केस आपको तभी दिखेगा जब "
    "वह आपके दायरे में हो।",
    {},
)

UNKNOWN = {
    "en": "I can't confirm that from the Kumbh Milan help content, so I won't guess. Please ask your station "
          "administrator or the help desk supervisor.",
    "hi": "मैं Kumbh Milan की सहायता सामग्री से इसकी पुष्टि नहीं कर सकता, इसलिए अनुमान नहीं लगाऊँगा। कृपया अपने स्टेशन "
          "प्रशासक या हेल्प डेस्क पर्यवेक्षक से पूछें।",
}
