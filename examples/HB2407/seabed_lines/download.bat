@echo off
cd /d "%~dp0"

gcloud storage cp ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20240924_t005406-t110610_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20240924_t120148-t232256_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20240925_t001826-t112431_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241002_t135743-t234243_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241003_t003819-t114024_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241003_t123556-t234213_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241004_t003243-t115842_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241004_t125413-t230613_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241005_t000153-t112045_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241005_t121603-t233100_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241006_t002635-t113223_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241006_t122745-t235831_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241007_t005353-t112511_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241007_t121110-t235210_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241008_t004743-t112346_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241008_t121922-t233654_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241009_t002158-t111514_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241009_t121041-t233326_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241010_t002818-t111427_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241010_t120920-t233311_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241011_t002801-t113150_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241011_t135328-t235639_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241012_t005144-t115311_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241012_t124819-t235027_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241013_t004543-t114752_evseabed.evl" ^
  .

if errorlevel 1 exit /b 1

gcloud storage cp ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241013_t124300-t235813_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241014_t004400-t111756_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241014_t121302-t233407_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241015_t002915-t114906_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241015_t124359-t234336_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241016_t003825-t113303_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241016_t122755-t232635_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241017_t002139-t112035_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241017_t121556-t232511_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241018_t001101-t114805_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241018_t124248-t233918_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241019_t002524-t114726_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241019_t123331-t234743_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241020_t003348-t115145_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241020_t123750-t232345_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241021_t000953-t114015_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241021_t122620-t231706_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241022_t001124-t120227_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241028_t140856-t231554_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241029_t001036-t110652_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241029_t120134-t230512_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241030_t000014-t115835_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241030_t125333-t233853_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241031_t003350-t111134_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241031_t120625-t231906_evseabed.evl" ^
  .

if errorlevel 1 exit /b 1

gcloud storage cp ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241101_t000511-t115150_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241101_t123757-t235034_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241102_t003645-t112203_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241102_t120808-t232956_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241103_t001603-t113551_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241103_t122151-t232440_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241104_t001041-t112755_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241104_t121355-t233344_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241105_t001944-t113752_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241105_t122402-t235757_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241106_t004353-t112001_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241106_t120559-t235919_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241107_t004526-t114120_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241107_t122724-t234530_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241108_t003122-t115912_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241108_t124512-t233058_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241109_t001651-t114307_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241109_t122853-t234858_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241110_t003442-t111439_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241110_t120019-t235747_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241111_t004327-t113557_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241111_t122156-t234820_evseabed.evl" ^
  "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines/d20241112_t003400-t124636_evseabed.evl" ^
  .
