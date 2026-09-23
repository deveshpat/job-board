# Hand labels (Claude, 2026-09-23) against Devesh's resume: entry level, ~1.4 yrs paid work, New Delhi, India,
# B.Sc. Physics; Python/PyTorch/LLM/FastAPI/Next.js/scraping. None = left unlabelled (ambiguous from the posting).
# Columns: tech, field, level, location, experience, work_mode, employment, degree, red_flags, relevant, skill(0-4)
N = None
JOBS = [
 ("T","backend",N,"local_office","4_7","onsite",N,N,"F","F",3),                 # J0 Sarvam backend 4-6y
 ("T","data_engineering","senior","local_office","4_7","hybrid",N,N,"F","F",1), # J1
 ("F","data_science","staff_plus","local_office","7_plus","onsite",N,N,"F","F",1),
 ("T","backend","staff_plus","local_office","7_plus",N,N,N,"F","F",1),
 ("F",N,"staff_plus","remote_open",N,"remote",N,N,"F","F",1),                   # J4 GitLab director, remote India
 ("F","non_engineering","staff_plus","local_office","4_7","onsite",N,N,"F","F",1),
 ("F","non_engineering","entry","local_office","0_2",N,N,N,"F","F",0),
 ("T",N,"staff_plus","local_office","7_plus",N,N,N,"F","F",1),
 ("F","non_engineering",N,"local_office","2_4",N,N,N,"F","F",0),
 ("F","non_engineering",N,"remote_open","7_plus","remote",N,N,"F","F",0),
 ("F","non_engineering",N,"local_office","not_stated","onsite",N,N,"F","F",0),  # J10
 ("F","non_engineering","senior","local_office","4_7",N,N,N,"F","F",0),
 ("T","devops_platform",N,"restricted","4_7","remote",N,N,"F","F",1),           # J12 JFrog US east coast
 ("F",N,"senior","restricted","4_7",N,N,N,"F","F",1),
 ("F",N,N,N,N,N,N,N,"T","F",N),                                                 # J14 "talent community" = no real role
 ("F","non_engineering",N,"restricted","2_4",N,"contract",N,"F","F",0),
 ("T","devops_platform","senior","restricted","7_plus","remote",N,N,"F","F",1),
 ("T","other_engineering",N,"restricted","4_7","remote",N,N,"F","F",1),
 ("T","other_engineering","senior","restricted","4_7","remote",N,N,"F","F",1),  # J18 CET±3 only
 ("T","devops_platform",N,"restricted","0_2","remote",N,N,"F","F",2),           # J19 Canonical EMEA
 ("F",N,"senior","restricted","4_7",N,N,N,"F","F",1),                           # J20
 ("F","non_engineering",N,"restricted","7_plus",N,N,N,"F","F",0),
 ("T",N,"senior","restricted","4_7",N,N,N,"F","F",2),
 ("F","non_engineering",N,"restricted","2_4",N,N,N,"F","F",0),
 ("T",N,"senior","restricted","4_7",N,N,"cs_engineering_degree","F","F",1),
 ("F","non_engineering","staff_plus","restricted","7_plus","remote",N,N,"F","F",0),
 ("F","non_engineering","senior","restricted",N,N,N,N,"F","F",0),
 ("T","backend","staff_plus","restricted","7_plus",N,N,N,"F","F",1),
 ("F","non_engineering","staff_plus","restricted",N,"onsite",N,N,"F","F",0),
 ("T",N,"staff_plus","restricted",N,"hybrid",N,"any_degree_or_equivalent","F","F",2),  # J29 Dublin + visa
 ("T",N,"staff_plus","restricted","4_7","hybrid",N,N,"F","F",1),                # J30
 ("T","full_stack","senior","restricted","2_4","remote",N,N,"F","F",2),
 ("T","devops_platform","senior","restricted","7_plus",N,N,N,"F","F",1),
 ("T","ai_ml_engineering",N,"restricted",N,N,N,N,"F","F",2),                    # J33 ET-CET only
 ("T",N,"senior","restricted",N,"hybrid",N,N,"F","F",1),
 ("T","ai_ml_engineering",N,"restricted","4_7",N,N,N,"F","F",2),
 ("T",N,"mid","restricted","2_4","hybrid",N,N,"F","F",3),                       # J36 Notion SF/NY
 ("F","non_engineering","senior","restricted","4_7","hybrid",N,"any_degree_or_equivalent","F","F",0),
 ("T",N,"senior","restricted",N,N,N,N,"F","F",2),
 ("F","research","staff_plus","restricted","4_7",N,N,N,"F","F",1),
 ("T","applied_ai_products",N,"restricted",N,N,N,N,"F","F",3),                  # J40
 ("T","devops_platform",N,"restricted",N,"hybrid",N,N,"F","F",2),
 ("F",N,"staff_plus","local_office","7_plus",N,N,"cs_engineering_degree","F","F",1),
 ("T","applied_ai_products",N,N,N,"remote",N,N,"F",N,3),                         # J43 US-ET overlap
 ("T","devops_platform",N,"local_office","4_7","onsite",N,N,"F","F",1),
 ("F","non_engineering","senior","restricted","4_7","remote",N,N,"F","F",0),
 ("T",N,N,"restricted","4_7","hybrid",N,N,"F","F",2),                           # J46
 ("T",N,"staff_plus","restricted",N,"remote",N,N,"F","F",2),
 ("T","backend",N,"restricted",N,"hybrid",N,N,"F","F",2),
 ("T",N,N,"restricted","4_7",N,N,"cs_engineering_degree","F","F",3),
 ("T","other_engineering",N,"restricted","4_7","hybrid",N,N,"F","F",0),         # J50
 ("T","backend","senior","restricted",N,"remote",N,N,"F","F",2),
 ("T","other_engineering","staff_plus","restricted","7_plus",N,N,N,"F","F",0),
 ("T","devops_platform","staff_plus","restricted","7_plus","remote",N,N,"F","F",1),
 ("F","non_engineering",N,"restricted",N,"hybrid",N,N,"F","F",0),
 ("F","non_engineering",N,"restricted",N,"remote",N,N,"F","F",0),               # J55
 ("F","non_engineering",N,N,"7_plus",N,N,N,"F","F",0),
 ("F","non_engineering",N,"restricted",N,N,N,N,"F","F",0),
 ("F",N,N,"restricted","2_4","hybrid",N,N,"F","F",1),
 ("F","non_engineering",N,"restricted","2_4",N,N,N,"F","F",0),
 ("T",N,"internship","remote_open","not_stated",N,"internship",N,"F","T",2),     # J60 Ritual intern, worldwide
 ("T","backend","senior","local_office","7_plus",N,N,N,"F","F",2),
 ("T",N,"internship",N,N,N,"internship",N,"F","T",3),                            # J62 AI-products intern, India
 ("T",N,N,N,"2_4",N,N,N,"F","F",0),
 ("T","frontend","entry",N,"2_4",N,N,N,"F","T",3),                               # J64 junior Next.js, India
 ("T",N,"internship","local_office","not_stated",N,"internship",N,"F",N,2),
 ("T","other_engineering","mid","local_office","2_4","hybrid",N,N,"F","F",1),
 ("T","devops_platform",N,"local_office","2_4","onsite",N,"cs_engineering_degree","F","F",1),
 ("T","backend",N,"remote_open","not_stated","remote","full_time",N,"F","F",1),
 ("T","devops_platform","senior","remote_open","7_plus","remote",N,N,"F","F",1),
 ("T","ai_ml_engineering","mid","local_office","2_4","onsite",N,N,"F","T",3),    # J70 Sarvam on-device inference
 ("T","other_engineering",N,"remote_open",N,"remote",N,N,"F","F",0),
 ("T",N,"mid","restricted","2_4","remote",N,N,"F","F",3),                        # J72 Pinterest remote US
 ("T","backend",N,"remote_open","not_stated","remote",N,N,"F","F",1),
 ("T","full_stack",N,"restricted",N,N,N,N,"F","F",3),                            # J74 US/Canada only
 ("T",N,"senior","remote_open","7_plus","remote",N,N,"F","F",1),
]
# Triage titles: (in_field, names_level_above); fields = AI/ML eng, applied AI, full stack, backend, data science.
F, T = False, True
TITLES = {
 0:(F,F),1:(F,F),2:(F,F),3:(F,T),4:(F,F),5:(F,T),6:(F,T),7:(F,F),8:(F,T),9:(T,F),10:(F,F),11:(T,F),12:(F,F),
 13:(F,F),14:(F,T),15:(F,F),16:(F,N),17:(F,F),18:(F,T),19:(F,T),20:(N,N),21:(F,F),22:(F,T),23:(N,F),24:(F,T),
 25:(F,F),26:(T,T),27:(F,T),28:(F,F),29:(N,F),30:(F,T),31:(F,N),32:(F,N),33:(N,F),34:(T,F),35:(F,F),36:(F,F),
 37:(F,T),38:(T,T),39:(T,T),40:(F,T),41:(F,F),42:(F,F),43:(F,T),44:(F,T),45:(F,N),46:(N,T),47:(F,F),48:(T,F),
 49:(F,F),50:(F,F),51:(T,F),52:(F,N),53:(F,F),54:(T,T),55:(N,T),56:(N,T),57:(F,T),58:(N,T),59:(F,F),60:(N,T),
 61:(F,N),62:(N,F),63:(N,T),64:(F,F),65:(F,T),66:(F,T),67:(N,F),68:(T,T),69:(T,T),70:(N,F),71:(F,N),72:(T,T),
 73:(N,T),74:(F,N),75:(T,F),76:(F,F),77:(F,T),78:(T,F),79:(F,F),80:(F,T),81:(T,F),82:(F,T),83:(F,F),84:(N,T),
 85:(F,F),86:(F,F),87:(T,T),88:(F,F),89:(F,N),90:(F,T),91:(F,F),92:(F,F),93:(F,N),94:(T,F),95:(N,T),96:(T,F),
 97:(N,T),98:(F,F),99:(F,T),100:(T,F),101:(N,T),102:(F,N),103:(N,N),104:(F,F),105:(F,N),106:(F,T),107:(F,F),
 108:(F,F),109:(F,F),110:(F,F),111:(T,T),112:(F,T),113:(F,F),114:(F,N),115:(F,T),116:(F,F),117:(F,T),118:(F,T),
 119:(F,F),120:(T,F),121:(F,F),122:(F,N),123:(F,N),124:(F,T),125:(F,F),126:(F,T),127:(F,F),128:(F,T),129:(T,F),
 130:(T,T),131:(F,T),132:(T,F),133:(F,N),134:(N,N),135:(F,T),136:(F,F),137:(F,T),138:(N,F),139:(F,T),140:(F,T),
 141:(F,F),142:(N,T),143:(F,F),144:(F,T),145:(F,F),146:(F,N),147:(N,N),148:(T,T),149:(F,N),150:(T,T),151:(T,T),
 152:(T,F),153:(F,N),154:(F,T),155:(F,F),156:(F,F),157:(F,F),158:(T,F),159:(F,F),160:(T,T),161:(F,T),162:(F,N),
 163:(N,T),164:(T,F),165:(F,T),166:(F,N),167:(F,N),168:(F,N),169:(F,T),170:(F,F),171:(F,F),172:(F,F),173:(T,F),
 174:(N,F),175:(N,T),176:(T,T),177:(F,T),178:(F,T),179:(N,T),180:(T,F),181:(F,F),182:(F,F),183:(F,F),184:(N,T),
 185:(F,F),186:(F,F),187:(F,F),188:(T,F),189:(F,N),190:(F,N),191:(F,N),192:(T,T),193:(N,T),194:(T,T),195:(N,T),
 196:(F,F),197:(T,F),198:(T,F),199:(F,F),
}
