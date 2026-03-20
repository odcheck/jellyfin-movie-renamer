import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock


def load_module():
    sys.argv = ['movie_renamer.py']
    spec = importlib.util.spec_from_file_location(
        'movie_renamer',
        '/home/ben/git/jellyfin-movie-renamer/movie_renamer.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MovieRenamerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_clean_search_term_strips_release_noise(self):
        cleaned = self.mod.clean_search_term(
            'Jericho_Mansions_-_German_-_2003_-_DVDRiP_-_XviD_- (2004)'
        )
        self.assertEqual(cleaned, 'Jericho Mansions')

    def test_clean_search_term_strips_release_groups_without_dropping_title_words(self):
        self.assertEqual(
            self.mod.clean_search_term('TRON.Legacy.DVDRip.LD.German.PROPER.XViD-EPiC - Le'),
            'TRON Legacy'
        )
        self.assertEqual(
            self.mod.clean_search_term('Panik im Jahre Null (1963) DVDRiP XviD - GMA - pij'),
            'Panik im Jahre Null'
        )

    def test_expand_search_term_variants_handles_next_chapter(self):
        variants = self.mod.expand_search_term_variants(
            'Der letzte Exorzismus The next Chapter'
        )
        self.assertIn('Der letzte Exorzismus 2', variants)

    def test_generic_imdb_title_is_rejected(self):
        self.assertTrue(self.mod.is_generic_imdb_title('Besetzung und Crew-Mitglieder'))
        self.assertFalse(self.mod.is_generic_imdb_title('Der letzte Exorzismus 2'))

    def test_numeric_titles_do_not_match_wrong_number(self):
        self.assertEqual(
            self.mod.title_match_score('Ice Age 2', '2012: Ice Age'),
            0
        )
        self.assertGreater(
            self.mod.title_match_score('Ice Age 2', "Ice Age 2 - Jetzt taut's"),
            0
        )

    def test_word_order_must_stay_consistent(self):
        self.assertEqual(
            self.mod.title_match_score('mad max 2', 'Mad 2 the Max'),
            0
        )
        self.assertGreater(
            self.mod.title_match_score('mad max 2 the road warrior', 'Mad Max 2'),
            0
        )

    def test_same_tail_different_head_is_rejected(self):
        self.assertEqual(
            self.mod.title_match_score('Wolverine Weg des Kriegers', 'Anuk Der Weg des Kriegers'),
            0
        )
        self.assertGreater(
            self.mod.title_match_score('Wolverine Weg des Kriegers', 'The Wolverine'),
            0
        )
        self.assertEqual(
            self.mod.title_match_score('Reality Xl Realitaet Ist Ein Traum', 'Reality'),
            0
        )

    def test_compound_franchise_tokens_still_match(self):
        self.assertGreater(
            self.mod.title_match_score('Duck Tales', 'DuckTales the Movie: Treasure of the Lost Lamp'),
            0
        )

    def test_feature_filter_rejects_games_and_podcasts(self):
        from bs4 import BeautifulSoup

        podcast_html = '''
        <div><div><div><a href="/title/tt38183794/?ref_=fn_t_5">Mad Max 2</a> Folge : Mad Max 2 ( 2021 )</div></div></div>
        '''
        game_html = '''
        <div><div><div><a href="/title/tt0795398/?ref_=fn_t_2">Ice Age 2 - Jetzt taut's</a> 2006 0 Videospiel</div></div></div>
        '''
        movie_html = '''
        <div><div><div><a href="/title/tt0438097/?ref_=fn_t_1">Ice Age 2 - Jetzt taut's</a> 2006 1h 31m</div></div></div>
        '''

        podcast_link = BeautifulSoup(podcast_html, 'html.parser').find('a')
        game_link = BeautifulSoup(game_html, 'html.parser').find('a')
        movie_link = BeautifulSoup(movie_html, 'html.parser').find('a')

        self.assertFalse(self.mod.is_feature_candidate_link(podcast_link))
        self.assertFalse(self.mod.is_feature_candidate_link(game_link))
        self.assertTrue(self.mod.is_feature_candidate_link(movie_link))

    def test_collect_metadata_title_clues_prefers_titles_over_plot_noise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            metadata_path = os.path.join(temp_dir, 'movie.xml')
            with open(metadata_path, 'w', encoding='utf-8') as handle:
                handle.write(
                    '<Title>\n'
                    '  <LocalTitle>Die Kammer der toten Kinder</LocalTitle>\n'
                    '  <OriginalTitle>La Chambre Des Morts</OriginalTitle>\n'
                    '  <Genre>Thriller</Genre>\n'
                    '  <Country>Frankreich</Country>\n'
                    '  <Description>Mitten in der Nacht überfahren zwei arbeitslose Computerprogrammierer einen Mann.</Description>\n'
                )

            clues = self.mod.collect_metadata_title_clues(temp_dir, debug=False)
            self.assertIn('Die Kammer der toten Kinder', clues)
            self.assertIn('La Chambre Des Morts', clues)
            self.assertNotIn('Thriller', clues)
            self.assertNotIn('Frankreich', clues)

    def test_collect_metadata_title_clues_ignores_xml_tags_and_desktop_ini_noise(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            metadata_path = os.path.join(temp_dir, 'movie.xml')
            ini_path = os.path.join(temp_dir, 'desktop.ini.txt')
            with open(metadata_path, 'w', encoding='utf-8') as handle:
                handle.write(
                    '<LocalTitle>Reality Xl - Realitaet Ist Ein Traum</LocalTitle>\n'
                    '<MPAARating>Unrated</MPAARating>\n'
                    '<IMDB></IMDB>\n'
                )
            with open(ini_path, 'w', encoding='utf-8') as handle:
                handle.write(
                    'HotKey=0\n'
                    'IconFile=C:\\Windows\\system32\\shell32.dll\n'
                )

            clues = self.mod.collect_metadata_title_clues(temp_dir, debug=False)
            self.assertIn('Reality Xl Realitaet Ist Ein Traum', clues)
            self.assertNotIn('MPAARating Unrated MPAARating', clues)
            self.assertFalse(any('HotKey' in clue for clue in clues))
            self.assertFalse(any('IconFile' in clue for clue in clues))

    def test_collect_imdb_id_clues_from_scene_nfo(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            nfo_path = os.path.join(temp_dir, 'dirtydub-wir-x264.nfo.txt')
            with open(nfo_path, 'w', encoding='utf-8') as handle:
                handle.write('www.imdb.com/title/tt6857112\n')

            imdb_ids = self.mod.collect_imdb_id_clues(temp_dir, debug=False)
            self.assertEqual(imdb_ids, ['tt6857112'])

    def test_dvd_and_iso_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_ts = os.path.join(temp_dir, 'VIDEO_TS')
            os.makedirs(video_ts, exist_ok=True)
            with open(os.path.join(video_ts, 'VIDEO_TS.IFO'), 'w', encoding='utf-8') as handle:
                handle.write('dvd marker')
            with open(os.path.join(temp_dir, 'movie.iso'), 'w', encoding='utf-8') as handle:
                handle.write('iso marker')

            self.assertTrue(self.mod.has_dvd_structure(temp_dir))
            self.assertTrue(self.mod.has_iso_image(temp_dir))

    def test_dvd_detection_allows_flat_video_ts_files_in_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, 'VIDEO_TS.IFO'), 'w', encoding='utf-8') as handle:
                handle.write('dvd marker')
            with open(os.path.join(temp_dir, 'VTS_01_1.VOB'), 'w', encoding='utf-8') as handle:
                handle.write('dvd marker')

            self.assertTrue(self.mod.has_dvd_structure(temp_dir))

    def test_detect_special_skip_reason_for_nested_multi_movie_dvd(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = os.path.join(temp_dir, 'Ingmar.Bergman.Special.DVD', 'VIDEO_TS')
            os.makedirs(nested, exist_ok=True)
            with open(os.path.join(nested, 'VIDEO_TS.IFO'), 'w', encoding='utf-8') as handle:
                handle.write('dvd marker')

            reason = self.mod.detect_special_skip_reason(
                temp_dir,
                clue_years=['1960', '1963']
            )
            self.assertEqual(
                reason,
                'Likely multi-movie DVD set with multiple years; skipping single-title rename'
            )

    def test_suggestion_video_items_can_be_allowed_for_metadata_id_recovery(self):
        data = {
            'd': [
                {'id': 'tt0156146', 'l': 'Tycus', 'y': 1999, 'qid': 'video'},
                {'id': 'tt0795398', 'l': "Ice Age 2 - Jetzt taut's", 'y': 2006, 'qid': 'videoGame'},
            ]
        }

        disallowed = self.mod.extract_imdb_suggestion_candidates('Tycus', data)
        allowed = self.mod.extract_imdb_suggestion_candidates('Tycus', data, allow_video=True)

        self.assertEqual(disallowed, [])
        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]['imdb_id'], 'tt0156146')

    def test_choose_preferred_local_title_keeps_clean_localized_title(self):
        candidate = {
            'title': 'Silent Running',
            'title_en': 'Silent Running',
            'year': '1972',
            'imdb_id': 'tt0067756',
        }
        chosen = self.mod.choose_preferred_local_title(
            candidate,
            ['Lautlos im Weltraum', 'False'],
            matched_search_term='Lautlos im Weltraum'
        )
        self.assertEqual(chosen, 'Lautlos im Weltraum')

    def test_detect_extra_type_from_folder_and_filename(self):
        self.assertEqual(
            self.mod.detect_extra_type('Sample', 'coincidence-life-720p-sample.mkv'),
            'sample'
        )
        self.assertEqual(
            self.mod.detect_extra_type('', 'movie-trailer.mkv'),
            'trailer'
        )
        self.assertIsNone(
            self.mod.detect_extra_type('', 'Wolverine Weg des Kriegers.avi')
        )

    def test_franchise_number_helpers(self):
        self.assertEqual(self.mod.extract_franchise_number('Die Unendliche Geschichte 2'), 2)
        self.assertEqual(self.mod.extract_franchise_number('Drachenzaehmen 1'), 1)
        self.assertEqual(self.mod.candidate_franchise_number('The NeverEnding Story II: The Next Chapter'), 2)
        self.assertEqual(self.mod.candidate_franchise_number('The NeverEnding Story'), None)

    def test_collects_year_and_title_clues_from_subfolder_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            subdir = os.path.join(temp_dir, 'Uploader.Presents-Die.Unfassbaren.2013.German.EXTE')
            os.makedirs(subdir, exist_ok=True)
            with open(os.path.join(subdir, 'movie.avi'), 'w', encoding='utf-8') as handle:
                handle.write('placeholder')

            years = self.mod.collect_local_year_clues(temp_dir, debug=False)
            clues = self.mod.collect_local_search_clues(temp_dir, 'die unfassbaren', debug=False)

            self.assertIn('2013', years)
            self.assertTrue(any('Die Unfassbaren' in clue for clue in clues))

    def test_apply_preferred_output_title_uses_wikidata_german_title(self):
        movie_info = {
            'title': 'Premonition',
            'title_en': 'Premonition',
            'year': '2007',
            'imdb_id': 'tt0477071',
        }

        with mock.patch.object(self.mod, 'fetch_wikidata_german_title', return_value='Die Vorahnung'):
            localized = self.mod.apply_preferred_output_title(
                movie_info,
                'Premonition',
                metadata_titles=[],
                clue_terms=[],
                debug=False
            )

        self.assertEqual(localized['title'], 'Die Vorahnung')
        self.assertEqual(localized['title_de'], 'Die Vorahnung')

    def test_apply_preferred_output_title_keeps_matching_metadata_title(self):
        movie_info = {
            'title': 'Elsewhere',
            'title_en': 'Elsewhere',
            'year': '2009',
            'imdb_id': 'tt0874271',
        }

        with mock.patch.object(self.mod, 'fetch_wikidata_german_title', return_value='Small Town Secret'):
            localized = self.mod.apply_preferred_output_title(
                movie_info,
                'elsewhere.(2009)',
                metadata_titles=['Elsewhere', 'Small Town Secret'],
                clue_terms=[],
                debug=False
            )

        self.assertEqual(localized['title'], 'Elsewhere')
        self.assertEqual(localized['title_de'], 'Elsewhere')

    def test_build_raw_search_variants_keeps_dash_prefix(self):
        variants = self.mod.build_raw_search_variants(
            'Duck Tales - Jäger des verlorenen Schatzes'
        )
        self.assertIn('Duck Tales', variants)

    def test_clean_search_term_splits_glued_title_parts(self):
        self.assertEqual(
            self.mod.clean_search_term('Die_JungfrauenquelleDas_SchweigenI (1960)'),
            'Die Jungfrauenquelle Das Schweigen I'
        )


if __name__ == '__main__':
    unittest.main()
