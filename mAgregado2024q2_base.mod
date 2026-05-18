
// Replicando o modelo do BCB (Relatório de Inflação 2024/06)
//
// Junho de 2024
//
// Filipe Stona
//
 
close all;
 
// Variáveis = 39
var piL_t piI_t inflt_focus_t4 piStar_t  delta_e_hat ht piM_t // 7
  climaA climaB dEL_t dLA_t climaSq_t // 5
  piAgro piMetal piEnergia // 3
  rt_hat rp_hat hStar_t //3
  st_h it_focus_t4 rr_barIS rr_barTrend rr_hatIS // 5
  it rt_barTaylor piMeta_t rt_hatTaylor // 4
  delta_e e_ppct it_dif it_star cdst // 5
  Etpit inflt_desvio // 2
  femp fcaged fnuci fpib brent_t; //5
 
varexo eps_piL //
  eps_h eps_h2008 eps_h2020  esp_rrIS eps_i eps_rrTaylor //
  eps_e eps_it eps_ei //
  eps_pib eps_nuci eps_emp eps_caged //
  eps_piA eps_piM eps_piE eps_brent //
  eps_hStar eps_it_star eps_cds eps_clima eps_EL eps_LA //
  eps_rp eps_meta eps_monit eps_rrBar;
 
parameters bbeta1 bbeta2 bbeta3 bbeta4 bbeta5 //
           aalpha1L aalpha1I aalpha2 aalpha3 aalpha4 aalpha5 aalpha6 aalpha3b//
           aalpha1M aalpha2I aalpha1B aalpha1D aalpha2D aalpha3D aalpha4D aalpha5D gamBrent iss_star//
           wa we wm //
           gam1Cambio gam2Cambio gam3Cambio //
           ttheta1 ttheta2 ttheta3 //
           pphi1 pphi2 pphi3 ddelta //
           ssigma_h pi_meta_ss pi_star_ss e_ppc ssigna_it ssigma_e//
           ggamma_caged ggamma_emp ggamma_nuci oomegaL rss; //
 
oomegaL = 1 - 0.259;
 
// curva IS
bbeta1  = 0.85;
bbeta2  = 0.44;
bbeta3  = 0.030;
bbeta4  = 0.054;
bbeta5  = 0.84;
 
wa = 0.63;
we = 0.19; // .19
wm = 1 - wa - we;
 
// curva de Phillips
aalpha1L = 0.24;
aalpha1I = 0.38;
aalpha2  = 0.023;
aalpha3  = 0.011;
aalpha4  = 0.12;
aalpha5  = 0.0012;
aalpha6  = 0.0007;
aalpha3b = 0.00046949;
 
// curva monit
aalpha1M = 0.14748;
aalpha2I = 0.57153;
aalpha1D = 0.73093;
aalpha2D = 2.2205e-14;
aalpha3D = 2.2313e-14;
aalpha4D = 0.00059036;
aalpha5D = 0.015821;
aalpha1B = 0.56428;
gamBrent = 0.29095;
 
gam1Cambio = 1.6135e-10;
gam2Cambio = 1.6377e-10;
gam3Cambio = 1.6069e-10;
 
// Regra de Taylor
ttheta1 =  1.48;
ttheta2 = -0.58;
ttheta3 =  2.03;
 
// Expectativa de inflaçao
pphi1 = 0.75;
pphi2 = 0.11;
pphi3 = 0.021;
 
//outros
ddelta       = 1.90;
ggamma_emp   = 1.10;
ggamma_nuci  = 1.87;
ggamma_caged = 0.69;
 
ssigma_h  = 1.09; // variância do erro de mensuração das variáveis observavéis do hiato
ssigna_it = 1.00;
ssigma_e  =  1.000;
 
pi_meta_ss = 3;
pi_star_ss = 2;
e_ppc = (pi_meta_ss - pi_star_ss)/4;
 
// steady state
iss_star = 1.2;
rss      = 4;
 
model;
 
// 1. Curva de Phillips
piL_t = aalpha1L * piL_t(-1) + aalpha1I * inflt_desvio/4 +//
        (1 - aalpha1L - aalpha1I) * inflt_focus_t4/4 + aalpha2 * piStar_t + aalpha3 * delta_e_hat(-1) + aalpha3b * delta_e_hat(-2) + //
        aalpha4 * ht + climaA - climaB + eps_piL;
 
//1a. Clima terma A
climaA = ((aalpha5*dEL_t + aalpha6 * dLA_t)*climaSq_t + //
         (aalpha5*dEL_t(-1) + aalpha6 * dLA_t(-1))*climaSq_t(-1) + //
         (aalpha5*dEL_t(-2) + aalpha6 * dLA_t(-2))*climaSq_t(-2) )/3;
 
//1ab. Clima terma B
climaB = ((aalpha5*dEL_t(-3) + aalpha6 * dLA_t(-3))*climaSq_t(-3) + //
          (aalpha5*dEL_t(-4) + aalpha6 * dLA_t(-4))*climaSq_t(-4) + //
          (aalpha5*dEL_t(-5) + aalpha6 * dLA_t(-5))*climaSq_t(-5) )/3;
 
//1c. Indice de Commodities (IC)
piStar_t = wa*piAgro + wm*piMetal + we*piEnergia;
 
//1d. IPCA cheio
piI_t = oomegaL * piL_t + (1 - oomegaL) * piM_t;
 
//2. Curva IS
ht = bbeta1*ht(-1) - bbeta2*rt_hat(-1)/4 - bbeta3*rp_hat + bbeta4*hStar_t + st_h + eps_h2008 + eps_h2020;
 
//2.1. Hiato da taxa de juros real
rt_hat = it_focus_t4 - inflt_focus_t4 - rr_barIS;
 
//2.2. pesistência dos choques
st_h = bbeta5*st_h(-1) + eps_h;
 
//2.3. juros real de equilibrio
rr_barIS = rr_barTrend + rr_hatIS;
 
//2.4. persistencia da taxa neutra
rr_hatIS = rr_hatIS(-1) + esp_rrIS;
 
//2.5. Selic focus - expectativa de juros
it_focus_t4 =  (0.5*it + it(1) + it(2) + it(3) + 0.5* it(4))/4 + ssigna_it * eps_it;
 
//3. Regra de Taylor
it = ttheta1 * it(-1) + ttheta2 * it(-2) + //
     (1 - ttheta1 - ttheta2) * ( rt_barTaylor + piMeta_t + ttheta3 * (inflt_focus_t4 - piMeta_t) ) + //
     eps_i;
 
//3.1. Juros neuto da taylor
rt_barTaylor = rr_barTrend + rt_hatTaylor;
 
//3.2. Persistencia do juros neutro taylor
rt_hatTaylor = rt_hatTaylor(-1) + eps_rrTaylor;
 
//4. UIP
delta_e = e_ppct - ddelta * (it_dif - it_dif(-1)) +  ssigma_e*eps_e;
 
//4.1. diferencial de juros
it_dif = it - it_star - cdst;
 
//4.2. cambio de longo prazo
e_ppct = (piMeta_t - pi_star_ss)/4;
 
//4.3. desvio do câmbio ao câmbio de LP
delta_e_hat = delta_e - e_ppct;
 
//5. expectativa de inflação
inflt_focus_t4 = pphi1 * inflt_focus_t4(-1) + pphi2 * Etpit + //
                               pphi3 * inflt_desvio + (1 - pphi1 - pphi2 - pphi3)*piMeta_t +//
                              eps_ei;
 
// 5.1. Expectativa consistente com o modelo (fwd)
Etpit = piI_t(1) + piI_t(2) + piI_t(3) + piI_t(4);
 
//5.2. Desvio da inflação passada (bwd)
inflt_desvio = piI_t(-1) + piI_t(-2) + piI_t(-3) + piI_t(-4);
 
//6 - 9. Informações do Hiato
fpib  = ht + ssigma_h * eps_pib;
fnuci/ggamma_nuci = ht + ssigma_h * eps_nuci;
femp/ggamma_emp = ht(-1) + ssigma_h * eps_emp;
fcaged/ggamma_caged = ht(-1) + ssigma_h * eps_caged;
 
// 26 - 39. Processos exógenos
piAgro         = gam1Cambio*(delta_e_hat) + eps_piA;
piMetal        = gam2Cambio*(delta_e_hat) + eps_piM;
piEnergia      = gamBrent*(brent_t) + gam3Cambio*(delta_e_hat) + (1-gamBrent)*eps_piE;
brent_t        = eps_brent;
hStar_t        = 0.5*hStar_t(-1) + eps_hStar;
it_star        = it_star(-1) + eps_it_star;
cdst           = 0.99*cdst(-1) + eps_cds;
climaSq_t      = 0.99*climaSq_t(-1) + eps_clima;
dEL_t          = dEL_t(-1) + eps_EL;
dLA_t          = dLA_t(-1) + eps_LA;
rr_barTrend    = (1 - 0.99)*rss + 0.99*rr_barTrend(-1) + eps_rrBar;
rp_hat         = 0.99*rp_hat(-1) + eps_rp;
piMeta_t       = piMeta_t(-1) + eps_meta;
piM_t          = aalpha1M*piM_t(-1) + aalpha2I *inflt_desvio/4 +//
                          (1 - aalpha1M - aalpha2I)*piMeta_t/4 + //
                          0.25*aalpha1D*(delta_e_hat) + 0.25*aalpha1B*(brent_t) +
                            aalpha3D*(delta_e_hat(-2)) + aalpha2D*(delta_e_hat(-1)) +
                            aalpha4D*(delta_e_hat(-3)) + aalpha5D*(delta_e_hat(-4)) +//
                          eps_monit;
 
end;
 
steady_state_model;
piMeta_t       = pi_meta_ss;
piL_t          = piMeta_t/4;
piI_t          = piMeta_t/4;
inflt_focus_t4 = piMeta_t;
piStar_t       = 0;
e_ppct         = e_ppc;
delta_e_hat    = 0;
delta_e        = e_ppc;
ht             = 0;
piM_t          = piMeta_t/4;
climaA         = 0;
climaB         = 0;
dEL_t          = 0;
dLA_t          = 0;
climaSq_t      = 0;
piAgro         = 0;
piMetal        = 0;
piEnergia      = 0;
rt_hat         = 0;
rp_hat         = 0;
hStar_t        = 0;
st_h           = 0;
rr_barTrend    = rss;
rr_hatIS       = 0;
rr_barIS       = rr_barTrend + rr_hatIS;
rt_hatTaylor   = 0;
rt_barTaylor   = rr_barTrend + rt_hatTaylor;
it             = rt_barTaylor + piMeta_t;
it_focus_t4    = it;
it_star        = iss_star;
cdst           = 0;
it_dif         = it - it_star;
Etpit          = piMeta_t;
inflt_desvio   = piMeta_t;
femp           = 0;
fcaged         = 0;
fnuci          = 0;
fpib           = 0;
brent_t        = 0;
end;
 
steady;
check;
 
shocks; //21
var eps_piL          ; stderr 1;
var eps_h            ; stderr 1;
var eps_h2008        ; stderr 1;
var eps_h2020        ; stderr 1;
var esp_rrIS         ; stderr 1;
var eps_i            ; stderr 1;
var eps_rrTaylor     ; stderr 1;
var eps_e            ; stderr 1;
var eps_it           ; stderr 1;
var eps_ei           ; stderr 1;
var eps_pib          ; stderr 1;
var eps_nuci         ; stderr 1;
var eps_emp          ; stderr 1;
var eps_caged        ; stderr 1;
var eps_piA          ; stderr 1;
var eps_piM          ; stderr 1;
var eps_piE          ; stderr 1;
var eps_brent        ; stderr 1;
var eps_hStar        ; stderr 1;
var eps_it_star      ; stderr 1;
var eps_cds          ; stderr 1;
var eps_clima        ; stderr 1;
var eps_EL           ; stderr 1;
var eps_LA           ; stderr 1;
var eps_rp           ; stderr 1;
var eps_meta         ; stderr 1;
var eps_monit        ; stderr 1;
var eps_rrBar        ; stderr 1;
end;
 
%varobs piL_t piI_t piMeta_t  inflt_focus_t4  it_focus_t4 piStar_t //
%       delta_e climaSq_t dEL_t dLA_t fpib  fcaged  fnuci femp  //
%       rp_hat  ie_hat  hStar_t it  it_star cdst; //20 var
 
%calib_smoother(datafile='datsimul_bcb4.xlsx',diffuse_filter, filtered_vars, //
%               filter_step_ahead = [1]);
 
//[1 1 1 1] - oo_.irfs.it_dif_eps_i(1:4)
stoch_simul(order = 1, noprint, nomoments, nograph, irf = 16) ;
 