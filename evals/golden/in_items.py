"""The ten synthetic Indian golden items (SPEC 12: 10 Indian tenders -- CPPP, GeM, state).

Five GeM bids, three CPPP (eProcure) tenders and two state GePNIC tenders in the
Hindi/English mix those portals actually publish. Every notice was written for this
eval; none reproduces a real tender.

Each item's eligibility block states the three numbers SPEC 12's pass bar names --
average annual turnover, EMD and years of experience -- in one clause each, so
`evals/run.py` can measure exact-match extraction of them. Those clauses are never among
an item's deliberate misses: the recall gap is always somewhere else.

`evals/golden/in/gem/` is M3-04's separate GeM eligibility golden set (bid PDFs with
`*.expected.json` labels) and is untouched by this file.
"""

from __future__ import annotations

from make_golden import PROSE, Item

# Devanagari the portals really print next to the English
ATC = "अतिरिक्त नियम और शर्तें"  # Additional terms and conditions
NIVIDA = "निविदा सूचना"  # Tender notice
PATRATA = "पात्रता मानदंड"  # Eligibility criteria

IN_ITEMS: list[Item] = [
    # --- GeM -----------------------------------------------------------------------------
    Item(
        slug="in/gem_desktops",
        notice_id="GEM/2026/B/4410021",
        region="in",
        notice_type="bid",
        source="synthetic GeM bid for desktop computers (evals/golden/in/gem_desktops)",
        misses=("L-08",),
        over_extractions=((1, "Bid opening will be held online in the presence of authorised representatives", "submission"),),
        repeats=("L-05",),
        eligibility={
            "min_avg_turnover_inr": "4500000",
            "emd_amount_inr": "240000",
            "min_experience_years": 3,
            "mse_exemption_allowed": True,
            "startup_exemption_allowed": False,
            "requires_gem_registration": True,
        },
        pages=[
            (
                f"GeM BID GEM/2026/B/4410021 - DESKTOP COMPUTERS ({NIVIDA})",
                [
                    (PROSE, "Buyer: Ministry of Education, Directorate of Higher Education. Quantity 120 units."),
                    (PROSE, "Bid opening will be held online in the presence of authorised representatives."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 45,00,000 over the last three financial years."),
                    ("eligibility", "The bidder shall furnish an EMD of Rs. 2,40,000 through the GeM online payment gateway before the bid end date."),
                    ("eligibility", "The bidder shall have at least 3 years of past experience in supplying desktop computers to government buyers."),
                    ("eligibility", "MSEs registered under Udyam are exempted from the EMD on submission of a valid Udyam certificate; startups are not exempted for this bid."),
                    ("eligibility", "The bidder shall be a registered seller on the Government e-Marketplace with the relevant product category approved."),
                ],
            ),
            (
                f"{ATC} / ADDITIONAL TERMS AND CONDITIONS",
                [
                    ("shall", "1.1 The seller shall deliver all 120 desktop computers to the consignee locations within 45 days of the supply order."),
                    ("shall", "1.2 The seller shall provide a comprehensive on-site warranty of three years covering parts and labour."),
                    ("must", "1.3 The seller must supply units bearing a valid BIS registration mark for IT equipment."),
                    ("shall", "1.4 The seller shall install the operating system and the buyer's standard image on every unit before handover."),
                    ("should", "1.5 The seller should provide a single point of contact for warranty escalations across all consignee locations."),
                ],
            ),
            (
                "BID SUBMISSION AND EVALUATION",
                [
                    ("format", "All documents shall be uploaded in PDF format and shall not exceed 10 MB per file."),
                    ("submission", "The technical and financial bids shall be submitted online on the GeM portal before 05:00 PM IST on 20-11-2026."),
                    ("submission", "The bidder shall digitally sign every uploaded document with a Class 3 Digital Signature Certificate."),
                    ("submission", "The bidder shall upload the Udyam registration certificate where an MSE exemption is claimed."),
                    ("evaluation", "Bids will be evaluated on the lowest landed price (L1) among technically qualified bidders."),
                    ("evaluation", "Technical qualification will be assessed against the minimum specification sheet and the submitted BIS certificate."),
                ],
            ),
        ],
    ),
    Item(
        slug="in/gem_security_services",
        notice_id="GEM/2026/B/4471188",
        region="in",
        notice_type="bid",
        source="synthetic GeM bid for security services (evals/golden/in/gem_security_services)",
        misses=("L-11",),
        over_extractions=((1, "The contract period is two years extendable by one year on satisfactory performance", "submission"),),
        repeats=(),
        eligibility={
            "min_avg_turnover_inr": "12000000",
            "emd_amount_inr": "500000",
            "min_experience_years": 5,
            "mse_exemption_allowed": True,
            "startup_exemption_allowed": True,
            "requires_gem_registration": True,
        },
        pages=[
            (
                f"GeM BID GEM/2026/B/4471188 - MANPOWER SECURITY SERVICES ({NIVIDA})",
                [
                    (PROSE, "Buyer: Central Warehousing Corporation, Regional Office Chennai. 64 security personnel."),
                    (PROSE, "The contract period is two years extendable by one year on satisfactory performance."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 1,20,00,000 during the last three financial years."),
                    ("eligibility", "The bidder shall deposit an EMD of Rs. 5,00,000 online before the bid end date and time."),
                    ("eligibility", "The bidder shall have at least 5 years of experience providing security manpower to public sector undertakings."),
                    ("eligibility", "MSEs and recognised startups are exempted from the EMD on submission of the relevant certificate."),
                    ("eligibility", "The bidder shall hold a valid licence under the Private Security Agencies (Regulation) Act, 2005 for Tamil Nadu."),
                ],
            ),
            (
                f"{ATC} / SCOPE OF SERVICES",
                [
                    ("shall", "2.1 The agency shall deploy 64 trained security personnel across four warehouses on three shifts every day."),
                    ("shall", "2.2 The agency shall pay wages not less than the minimum wages notified for the state and produce proof by the 7th of each month."),
                    ("must", "2.3 The agency must deposit EPF and ESI contributions for every deployed person and furnish the monthly challans."),
                    ("shall", "2.4 The agency shall replace an absent guard within two hours of the reported absence."),
                    ("should", "2.5 The agency should provide an electronic attendance system with biometric verification at each gate."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "Scanned documents shall be legible and uploaded as a single PDF per requirement."),
                    ("submission", "Bids shall be submitted online on the GeM portal before 03:00 PM IST on 02-12-2026."),
                    ("submission", "All uploaded documents shall be digitally signed with a Class 3 DSC in the name of the authorised signatory."),
                    ("submission", "The bidder shall upload the PSARA licence, the PAN card and the GST registration certificate."),
                    ("evaluation", "Evaluation will follow the lowest total monthly cost among bidders meeting the eligibility criteria."),
                    ("evaluation", "The buyer will verify statutory compliance records for the last two years before award."),
                ],
            ),
        ],
    ),
    Item(
        slug="in/gem_network_switches",
        notice_id="GEM/2026/B/4502277",
        region="in",
        notice_type="bid",
        source="synthetic GeM bid for network switches (evals/golden/in/gem_network_switches)",
        misses=("L-07",),
        over_extractions=(
            (1, "Buyer: Indian Institute of Technology Kanpur, Computer Centre", "submission"),
        ),
        repeats=("L-09",),
        eligibility={
            "min_avg_turnover_inr": "30000000",
            "emd_amount_inr": "750000",
            "min_experience_years": 3,
            "mse_exemption_allowed": False,
            "startup_exemption_allowed": False,
            "requires_gem_registration": True,
        },
        pages=[
            (
                f"GeM BID GEM/2026/B/4502277 - CAMPUS NETWORK SWITCHES ({NIVIDA})",
                [
                    (PROSE, "Buyer: Indian Institute of Technology Kanpur, Computer Centre. 210 managed switches."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 3,00,00,000 in the last three financial years."),
                    ("eligibility", "The bidder shall furnish an EMD of Rs. 7,50,000 by online transfer before the bid end date."),
                    ("eligibility", "The bidder shall have at least 3 years of experience supplying managed network switches to educational or government institutions."),
                    ("eligibility", "No EMD exemption is available under this bid for MSEs or for recognised startups."),
                    ("eligibility", "The bidder shall submit an original equipment manufacturer authorisation certificate valid for this bid number."),
                ],
            ),
            (
                f"{ATC} / TECHNICAL REQUIREMENTS",
                [
                    ("shall", "3.1 The supplier shall deliver 210 managed switches to the campus store within 60 days of the supply order."),
                    ("shall", "3.2 The supplier shall provide five years of OEM back-to-back warranty with next business day replacement."),
                    ("must", "3.3 The supplier must ensure every switch supports IEEE 802.1X and IPv6 routing as specified in the technical sheet."),
                    ("shall", "3.4 The supplier shall commission the switches and hand over configuration backups for each device."),
                    ("should", "3.5 The supplier should train four campus engineers on the management console at no additional cost."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "Each uploaded document shall be a searchable PDF of not more than 20 MB."),
                    ("submission", "Bids shall be submitted on the GeM portal before 04:00 PM IST on 09-12-2026."),
                    ("submission", "The bidder shall digitally sign the technical and financial covers with a Class 3 DSC."),
                    ("submission", "The bidder shall upload the OEM authorisation certificate and the compliance statement against the technical sheet."),
                    ("evaluation", "Technically qualified bids will be ranked by the total landed price including installation."),
                    ("evaluation", "The buyer will reject any bid whose compliance statement deviates from the mandatory specifications."),
                ],
            ),
        ],
    ),
    Item(
        slug="in/gem_ambulance",
        notice_id="GEM/2026/B/4533901",
        region="in",
        notice_type="bid",
        source="synthetic GeM bid for ambulances (evals/golden/in/gem_ambulance)",
        misses=("L-06",),
        over_extractions=((1, "The estimated bid value is Rs. 4,80,00,000 inclusive of all taxes", "submission"),),
        repeats=(),
        eligibility={
            "min_avg_turnover_inr": "50000000",
            "emd_amount_inr": "960000",
            "min_experience_years": 5,
            "mse_exemption_allowed": True,
            "startup_exemption_allowed": False,
            "requires_gem_registration": True,
        },
        pages=[
            (
                f"GeM BID GEM/2026/B/4533901 - ADVANCED LIFE SUPPORT AMBULANCES ({NIVIDA})",
                [
                    (PROSE, "Buyer: Government of Odisha, Department of Health and Family Welfare. 24 vehicles."),
                    (PROSE, "The estimated bid value is Rs. 4,80,00,000 inclusive of all taxes."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 5,00,00,000 over the preceding three financial years."),
                    ("eligibility", "The bidder shall submit an EMD of Rs. 9,60,000 online before the bid end date."),
                    ("eligibility", "The bidder shall have at least 5 years of experience in supplying ambulances fabricated to AIS-125 standards."),
                    ("eligibility", "MSEs registered under Udyam are exempted from the EMD; startup exemption is not available for this bid."),
                    ("eligibility", "The bidder shall hold a valid type approval certificate from a notified testing agency for the offered vehicle."),
                ],
            ),
            (
                f"{ATC} / SUPPLY AND SERVICE",
                [
                    ("shall", "4.1 The supplier shall deliver 24 fully fabricated ambulances to the district headquarters within 120 days of the order."),
                    ("shall", "4.2 The supplier shall provide three years of comprehensive warranty covering the chassis and the medical equipment."),
                    ("must", "4.3 The supplier must ensure every vehicle carries the medical equipment list specified in Annexure II at handover."),
                    ("shall", "4.4 The supplier shall establish an authorised service point within 100 kilometres of each district headquarters."),
                    ("should", "4.5 The supplier should train two drivers and two paramedics per district on the installed equipment."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "All certificates shall be uploaded as PDF files of not more than 15 MB each."),
                    ("submission", "Bids shall be submitted on the GeM portal before 05:00 PM IST on 15-12-2026."),
                    ("submission", "The bidder shall sign the technical and financial covers with a Class 3 Digital Signature Certificate."),
                    ("submission", "The bidder shall upload the AIS-125 type approval certificate and the Udyam certificate where applicable."),
                    ("evaluation", "Bids will be evaluated on the lowest price per vehicle among technically qualified bidders."),
                    ("evaluation", "A physical inspection of a sample vehicle may be required before award."),
                ],
            ),
        ],
    ),
    Item(
        slug="in/gem_solar_rooftop",
        notice_id="GEM/2026/B/4560114",
        region="in",
        notice_type="bid",
        source="synthetic GeM bid for rooftop solar (evals/golden/in/gem_solar_rooftop)",
        misses=("L-12",),
        over_extractions=((1, "The scope covers 2.4 MW of rooftop capacity across nine buildings", "submission"),),
        repeats=("L-04",),
        eligibility={
            "min_avg_turnover_inr": "80000000",
            "emd_amount_inr": "1500000",
            "min_experience_years": 7,
            "mse_exemption_allowed": False,
            "startup_exemption_allowed": True,
            "requires_gem_registration": True,
        },
        pages=[
            (
                f"GeM BID GEM/2026/B/4560114 - ROOFTOP SOLAR PLANTS ({NIVIDA})",
                [
                    (PROSE, "Buyer: Bharat Heavy Electricals Limited, Hyderabad Unit."),
                    (PROSE, "The scope covers 2.4 MW of rooftop capacity across nine buildings."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 8,00,00,000 in each of the last three financial years."),
                    ("eligibility", "The bidder shall furnish an EMD of Rs. 15,00,000 through the online gateway before the bid end date."),
                    ("eligibility", "The bidder shall have at least 7 years of experience commissioning grid-connected rooftop solar plants."),
                    ("eligibility", "Recognised startups are exempted from the EMD; MSE exemption is not available under this bid."),
                    ("eligibility", "The bidder shall be empanelled with the state distribution company for net metering work."),
                ],
            ),
            (
                f"{ATC} / EXECUTION",
                [
                    ("shall", "5.1 The contractor shall design, supply, install and commission 2.4 MW of rooftop solar capacity within 180 days."),
                    ("shall", "5.2 The contractor shall guarantee a minimum plant availability of 97 percent over the five-year operation period."),
                    ("must", "5.3 The contractor must use modules and inverters listed in the Approved List of Models and Manufacturers."),
                    ("shall", "5.4 The contractor shall obtain net metering approval from the distribution company before commissioning."),
                    ("shall", "5.5 The contractor shall operate and maintain the plants for five years after commissioning."),
                    ("should", "5.6 The contractor should provide a remote monitoring portal with daily generation reports."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "The technical cover shall not exceed 60 pages excluding datasheets."),
                    ("submission", "Bids shall be submitted on the GeM portal before 03:00 PM IST on 22-12-2026."),
                    ("submission", "The bidder shall digitally sign all covers with a Class 3 DSC and upload the empanelment letter."),
                    ("evaluation", "Evaluation will be on the lowest levelised cost per kWh over the five-year operation period."),
                    ("evaluation", "The buyer will verify commissioning certificates for at least 5 MW of completed rooftop capacity."),
                ],
            ),
        ],
    ),
    # --- CPPP / eProcure -------------------------------------------------------------------
    Item(
        slug="in/cppp_nhai_roadworks",
        notice_id="NHAI/RO-JAI/2026/EE/0117",
        region="in",
        notice_type="tender",
        source="synthetic CPPP (eProcure) NHAI works tender (evals/golden/in/cppp_nhai_roadworks)",
        misses=("L-10",),
        over_extractions=((1, "The estimated cost put to tender is Rs. 62,40,00,000", "submission"),),
        repeats=(),
        eligibility={
            "min_avg_turnover_inr": "312000000",
            "emd_amount_inr": "6240000",
            "min_experience_years": 5,
            "mse_exemption_allowed": False,
            "startup_exemption_allowed": False,
            "requires_cppp_enrolment": True,
        },
        pages=[
            (
                f"CPPP TENDER NHAI/RO-JAI/2026/EE/0117 - HIGHWAY STRENGTHENING ({NIVIDA})",
                [
                    (PROSE, "National Highways Authority of India, Regional Office Jaipur. Length 48.6 km on NH-52."),
                    (PROSE, "The estimated cost put to tender is Rs. 62,40,00,000."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 31,20,00,000 in the last three financial years."),
                    ("eligibility", "The bidder shall submit an EMD of Rs. 62,40,000 by RTGS or a bank guarantee valid for 180 days."),
                    ("eligibility", "The bidder shall have at least 5 years of experience executing highway strengthening works of similar nature."),
                    ("eligibility", "The bidder shall be enrolled on the Central Public Procurement Portal with a valid Class 3 Digital Signature Certificate."),
                    ("eligibility", "The bidder shall not be under any debarment or blacklisting by NHAI, MoRTH or any state PWD on the bid due date."),
                ],
            ),
            (
                "SCOPE OF WORK AND CONTRACT CONDITIONS",
                [
                    ("shall", "6.1 The contractor shall strengthen 48.6 km of the existing carriageway including shoulders and cross drainage works."),
                    ("shall", "6.2 The contractor shall complete the work within 18 months of the appointed date."),
                    ("must", "6.3 The contractor must establish a field laboratory at site and test materials at the frequency in the MoRTH specifications."),
                    ("shall", "6.4 The contractor shall maintain the completed work during a defect liability period of 60 months."),
                    ("shall", "6.5 The contractor shall deploy the key personnel named in the bid and seek written approval before any replacement."),
                    ("should", "6.6 The contractor should submit a monthly safety report covering incidents, near misses and corrective actions."),
                ],
            ),
            (
                "BID SUBMISSION AND EVALUATION",
                [
                    ("format", "Each uploaded file shall be in PDF format and shall not exceed 25 MB."),
                    ("submission", "Technical and financial bids shall be uploaded on the CPP Portal before 03:00 PM IST on 11-12-2026."),
                    ("submission", "The bidder shall digitally sign both covers with a Class 3 DSC issued to the authorised signatory."),
                    ("submission", "The bidder shall upload an affidavit of non-blacklisting and a power of attorney for the signatory."),
                    ("submission", "The original bank guarantee towards EMD shall reach the Regional Office before the bid opening date."),
                    ("evaluation", "Financial bids of technically qualified bidders only will be opened and the lowest evaluated bid will be considered."),
                    ("evaluation", "Technical evaluation will assess completed similar works, bid capacity and the deployment of key personnel."),
                ],
            ),
        ],
    ),
    Item(
        slug="in/cppp_railways_signalling",
        notice_id="RLY/SCR/SIG/2026/0043",
        region="in",
        notice_type="tender",
        source="synthetic CPPP railways signalling tender (evals/golden/in/cppp_railways_signalling)",
        misses=("L-13",),
        over_extractions=(
            (1, "South Central Railway, Signal and Telecommunication Department, Secunderabad", "submission"),
        ),
        repeats=("L-08",),
        eligibility={
            "min_avg_turnover_inr": "150000000",
            "emd_amount_inr": "3000000",
            "min_experience_years": 3,
            "mse_exemption_allowed": True,
            "startup_exemption_allowed": False,
            "requires_cppp_enrolment": True,
        },
        pages=[
            (
                f"CPPP TENDER RLY/SCR/SIG/2026/0043 - ELECTRONIC INTERLOCKING ({NIVIDA})",
                [
                    (PROSE, "South Central Railway, Signal and Telecommunication Department, Secunderabad. 11 stations."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 15,00,00,000 over the last three financial years."),
                    ("eligibility", "The bidder shall submit an EMD of Rs. 30,00,000 through the CPP Portal payment gateway or as a bank guarantee."),
                    ("eligibility", "The bidder shall have at least 3 years of experience commissioning electronic interlocking systems on Indian Railways."),
                    ("eligibility", "MSEs registered under Udyam are exempted from the EMD on submission of a valid certificate."),
                    ("eligibility", "The bidder shall hold a valid RDSO approval for the offered electronic interlocking system."),
                ],
            ),
            (
                "SCOPE OF WORK",
                [
                    ("shall", "7.1 The contractor shall supply, install, test and commission electronic interlocking at 11 stations."),
                    ("shall", "7.2 The contractor shall complete the work at each station within the traffic block periods allotted by the railway."),
                    ("must", "7.3 The contractor must obtain the Commissioner of Railway Safety sanction before commissioning each installation."),
                    ("shall", "7.4 The contractor shall provide two years of warranty and free maintenance after commissioning."),
                    ("shall", "7.5 The contractor shall train railway signalling staff on the installed system at each station."),
                    ("should", "7.6 The contractor should provide a spares holding plan for the warranty period."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "Uploaded documents shall be legible PDF files of not more than 20 MB each."),
                    ("submission", "Bids shall be submitted on the CPP Portal before 15:00 hours IST on 18-12-2026."),
                    ("submission", "Both covers shall be digitally signed with a Class 3 DSC in the name of the authorised signatory."),
                    ("submission", "The bidder shall upload the RDSO approval letter and the Udyam certificate where an exemption is claimed."),
                    ("evaluation", "Technically responsive bids will be ranked by the total evaluated cost including two years of maintenance."),
                    ("evaluation", "The railway will verify completion certificates for at least five commissioned interlocking installations."),
                ],
            ),
        ],
    ),
    Item(
        slug="in/cppp_cpwd_building",
        notice_id="CPWD/NDCC-II/2026/0288",
        region="in",
        notice_type="tender",
        source="synthetic CPPP CPWD building works tender (evals/golden/in/cppp_cpwd_building)",
        misses=("L-05",),
        over_extractions=((1, "The tender fee of Rs. 5,000 is payable online and is non-refundable", "submission"),),
        repeats=(),
        eligibility={
            "min_avg_turnover_inr": "90000000",
            "emd_amount_inr": "1800000",
            "min_experience_years": 5,
            "mse_exemption_allowed": False,
            "startup_exemption_allowed": False,
            "requires_cppp_enrolment": True,
        },
        pages=[
            (
                f"CPPP TENDER CPWD/NDCC-II/2026/0288 - OFFICE BUILDING RENOVATION ({NIVIDA})",
                [
                    (PROSE, "Central Public Works Department, New Delhi Central Circle II. Built-up area 18,400 square metres."),
                    (PROSE, "The tender fee of Rs. 5,000 is payable online and is non-refundable."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 9,00,00,000 in the last three financial years."),
                    ("eligibility", "The bidder shall furnish an EMD of Rs. 18,00,000 by online payment or a bank guarantee from a scheduled commercial bank."),
                    ("eligibility", "The bidder shall have at least 5 years of experience in building renovation works of comparable value."),
                    ("eligibility", "The bidder shall be enrolled on the CPP Portal and shall hold a valid Class 3 Digital Signature Certificate."),
                    ("eligibility", "The bidder shall submit a solvency certificate of Rs. 3,60,00,000 issued within the last twelve months."),
                ],
            ),
            (
                "SCOPE OF WORK",
                [
                    ("shall", "8.1 The contractor shall renovate 18,400 square metres of office space including civil, electrical and HVAC works."),
                    ("shall", "8.2 The contractor shall complete the work within 15 months from the date of the work order."),
                    ("must", "8.3 The contractor must obtain all statutory clearances including the fire safety certificate before handover."),
                    ("shall", "8.4 The contractor shall carry out the work in occupied premises in phases agreed with the engineer in charge."),
                    ("should", "8.5 The contractor should propose measures to limit dust and noise during working hours."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "Documents shall be uploaded as PDF files; each file shall not exceed 30 MB."),
                    ("submission", "Bids shall be uploaded on the CPP Portal before 15:30 hours IST on 05-01-2027."),
                    ("submission", "The technical and financial covers shall be digitally signed with a Class 3 DSC."),
                    ("submission", "The bidder shall upload an affidavit confirming that the firm is not blacklisted by any government department."),
                    ("evaluation", "The lowest evaluated financial bid among technically qualified bidders will be recommended for award."),
                    ("evaluation", "Technical evaluation will verify similar completed works, the solvency certificate and the bid capacity."),
                ],
            ),
        ],
    ),
    # --- state GePNIC ---------------------------------------------------------------------
    Item(
        slug="in/gepnic_tn_water_supply",
        notice_id="TWAD/CBE/2026/WS/0091",
        region="in",
        notice_type="tender",
        source="synthetic Tamil Nadu GePNIC tender (evals/golden/in/gepnic_tn_water_supply)",
        misses=("L-09",),
        over_extractions=((1, "The tender is invited in two-cover system through the Tamil Nadu tenders portal", "submission"),),
        repeats=(),
        eligibility={
            "min_avg_turnover_inr": "60000000",
            "emd_amount_inr": "1200000",
            "min_experience_years": 5,
            "mse_exemption_allowed": True,
            "startup_exemption_allowed": False,
            "requires_cppp_enrolment": False,
        },
        pages=[
            (
                f"GePNIC TENDER TWAD/CBE/2026/WS/0091 - WATER SUPPLY SCHEME ({NIVIDA})",
                [
                    (PROSE, "Tamil Nadu Water Supply and Drainage Board, Coimbatore Division. 42 village habitations."),
                    (PROSE, "The tender is invited in two-cover system through the Tamil Nadu tenders portal."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 6,00,00,000 during the last three financial years."),
                    ("eligibility", "The bidder shall remit an EMD of Rs. 12,00,000 online or furnish a bank guarantee valid for 120 days."),
                    ("eligibility", "The bidder shall have at least 5 years of experience executing rural water supply schemes."),
                    ("eligibility", "MSEs registered under Udyam are exempted from the EMD on production of a valid certificate."),
                    ("eligibility", "The bidder shall be registered as a Class I contractor with the Tamil Nadu Public Works Department."),
                ],
            ),
            (
                "SCOPE OF WORK",
                [
                    ("shall", "9.1 The contractor shall lay 96 kilometres of distribution mains and construct four overhead tanks."),
                    ("shall", "9.2 The contractor shall complete the entire scheme within 24 months from the date of agreement."),
                    ("must", "9.3 The contractor must use pipes bearing an ISI mark and produce test certificates for every consignment."),
                    ("shall", "9.4 The contractor shall restore all road cuttings to the satisfaction of the concerned local body."),
                    ("shall", "9.5 The contractor shall maintain the scheme for a defect liability period of 24 months after completion."),
                    ("should", "9.6 The contractor should engage local labour to the extent available at the work site."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "All documents shall be uploaded in PDF format; each file shall not exceed 10 MB."),
                    ("submission", "Bids shall be uploaded on the Tamil Nadu tenders portal before 03:00 PM IST on 27-11-2026."),
                    ("submission", "The technical and price covers shall be digitally signed with a Class 3 Digital Signature Certificate."),
                    ("submission", "The bidder shall upload the PWD registration certificate, the PAN card and the GST registration."),
                    ("evaluation", "Price covers of technically qualified bidders alone will be opened and the lowest quote accepted."),
                    ("evaluation", "The board will verify completion certificates for at least two rural water supply schemes of similar value."),
                ],
            ),
        ],
    ),
    Item(
        slug="in/gepnic_mh_it_services",
        notice_id="MAHAIT/PUN/2026/SVC/0210",
        region="in",
        notice_type="tender",
        source="synthetic Maharashtra GePNIC IT services tender (evals/golden/in/gepnic_mh_it_services)",
        misses=("L-07",),
        over_extractions=(
            (1, "Maharashtra Information Technology Corporation, Pune", "submission"),
        ),
        repeats=("L-11",),
        eligibility={
            "min_avg_turnover_inr": "25000000",
            "emd_amount_inr": "500000",
            "min_experience_years": 3,
            "mse_exemption_allowed": True,
            "startup_exemption_allowed": True,
            "requires_cppp_enrolment": False,
        },
        pages=[
            (
                f"GePNIC TENDER MAHAIT/PUN/2026/SVC/0210 - DATA CENTRE OPERATIONS ({NIVIDA})",
                [
                    (PROSE, "Maharashtra Information Technology Corporation, Pune. State data centre operations for three years."),
                    ("eligibility", f"{PATRATA}: The bidder shall have a minimum average annual turnover of Rs. 2,50,00,000 in the last three financial years."),
                    ("eligibility", "The bidder shall pay an EMD of Rs. 5,00,000 online through the Maharashtra tenders portal."),
                    ("eligibility", "The bidder shall have at least 3 years of experience operating a tier III or higher data centre."),
                    ("eligibility", "MSEs and recognised startups are exempted from the EMD on submission of the relevant certificate."),
                    ("eligibility", "The bidder shall hold a valid ISO 27001 certificate covering data centre operations."),
                ],
            ),
            (
                "SCOPE OF SERVICES",
                [
                    ("shall", "10.1 The service provider shall operate the state data centre on a 24x7 basis with a minimum of 18 deployed engineers."),
                    ("shall", "10.2 The service provider shall maintain 99.5 percent uptime for hosted services measured monthly."),
                    ("must", "10.3 The service provider must conduct a vulnerability assessment every quarter and close critical findings within 15 days."),
                    ("shall", "10.4 The service provider shall provide a monthly service level report within five working days of month end."),
                    ("should", "10.5 The service provider should propose automation that reduces manual operational effort year on year."),
                ],
            ),
            (
                "SUBMISSION AND EVALUATION",
                [
                    ("format", "The technical cover shall not exceed 80 pages excluding annexures."),
                    ("submission", "Bids shall be uploaded on the Maharashtra tenders portal before 05:00 PM IST on 08-12-2026."),
                    ("submission", "Both covers shall be digitally signed with a Class 3 DSC issued to the authorised signatory."),
                    ("submission", "The bidder shall upload the ISO 27001 certificate and three client reference letters."),
                    ("evaluation", "Evaluation will follow a quality and cost based selection with 70 percent weight on technical score."),
                    ("evaluation", "The technical score will consider relevant experience, the proposed team and the transition plan."),
                ],
            ),
        ],
    ),
]
